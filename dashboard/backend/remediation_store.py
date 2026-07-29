"""Persistence for remediation plans.

Postgres is mandatory by default. The JSON implementation is an explicit,
single-process development escape hatch only.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dashboard.backend.db import _libpq_kv, dsn_from_env, outputs_dir

logger = logging.getLogger(__name__)

TERMINAL = {"succeeded", "failed", "rolled_back", "rejected", "expired"}
APPROVABLE = {"proposed", "failed", "rolled_back"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utcnow()).isoformat()


def _remediation_dsn_from_env() -> str | None:
    if explicit := os.getenv("CAUSA_REMEDIATION_PG_DSN", "").strip():
        return explicit
    if password := os.getenv("REMEDIATION_PGPASSWORD", "").strip():
        pairs = {
            "host": os.getenv("REMEDIATION_PGHOST", os.getenv("PGHOST", "")),
            "port": os.getenv("REMEDIATION_PGPORT", os.getenv("PGPORT", "5432")),
            "dbname": os.getenv(
                "REMEDIATION_PGDATABASE", os.getenv("PGDATABASE", "causa")
            ),
            "user": os.getenv("REMEDIATION_PGUSER", "causa_remediator"),
            "password": password,
        }
        if pairs["host"]:
            return " ".join(_libpq_kv(key, value) for key, value in pairs.items())
    return dsn_from_env()


class RemediationStore:
    def __init__(self) -> None:
        self._dsn = _remediation_dsn_from_env()
        self._allow_file = os.getenv(
            "CAUSA_REMEDIATION_ALLOW_FILE_STORE", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._file = Path(
            os.getenv(
                "CAUSA_REMEDIATION_STORE",
                str(outputs_dir() / "c" / "remediation_plans.json"),
            )
        )
        if not self._dsn and not self._allow_file:
            raise RuntimeError(
                "Postgres is required for remediation; set PGHOST/CAUSA_PG_DSN "
                "or explicitly opt into the unsafe development file store with "
                "CAUSA_REMEDIATION_ALLOW_FILE_STORE=1"
            )
        self._ensure_schema()

    def _connect(self):
        import psycopg

        return psycopg.connect(self._dsn, autocommit=True)

    def _ensure_schema(self) -> None:
        if not self._dsn:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            if not self._file.exists():
                self._file.write_text("[]\n", encoding="utf-8")
            return
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT to_regclass('public.remediation_plans'),
                           to_regclass('public.remediation_events')
                    """
                ).fetchone()
                if not row or not all(row):
                    raise RuntimeError(
                        "remediation schema migration has not been applied"
                    )
        except Exception as exc:
            if not self._allow_file:
                raise RuntimeError("remediation Postgres schema is unavailable") from exc
            logger.warning("remediation schema ensure failed, using development file store: %s", exc)
            self._dsn = None
            self._file.parent.mkdir(parents=True, exist_ok=True)
            if not self._file.exists():
                self._file.write_text("[]\n", encoding="utf-8")

    def _load_file(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_file(self, rows: list[dict[str, Any]]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")

    def add_event(self, plan_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        event = {
            "id": str(uuid.uuid4()),
            "plan_id": plan_id,
            "event_type": event_type,
            "payload": payload or {},
            "created_at": _iso(),
        }
        if self._dsn:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO remediation_events (id, plan_id, event_type, payload, created_at)
                        VALUES (%s, %s, %s, %s::jsonb, %s)
                        """,
                        (
                            event["id"],
                            plan_id,
                            event_type,
                            json.dumps(payload or {}),
                            event["created_at"],
                        ),
                    )
                return
            except Exception as exc:
                logger.warning("event insert failed: %s", exc)
                if not self._allow_file:
                    raise RuntimeError("remediation event insert failed") from exc
        rows = self._load_file()
        for row in rows:
            if row.get("id") == plan_id:
                row.setdefault("events", []).append(event)
                break
        self._save_file(rows)

    def upsert_proposed(self, plan: dict[str, Any], *, force_new: bool = False) -> dict[str, Any]:
        incident_id = str(plan.get("incident_id") or "")
        plan_spec = plan.get("plan_spec") or (plan.get("details") or {}).get("plan_spec")
        if not force_new:
            existing = self.list_plans(incident_id=incident_id, status="proposed")
            if existing:
                row = existing[0]
                row.update(
                    {
                        "candidate_node": plan.get("candidate_node"),
                        "fault_class": plan.get("fault_class"),
                        "action": plan.get("action"),
                        "depth": plan.get("depth"),
                        "summary": plan.get("summary"),
                        "details": plan.get("details") or {},
                        "report_id": plan.get("report_id"),
                        "plan_spec": plan_spec,
                        "before_fingerprint": plan.get("before_fingerprint"),
                        "idempotency_key": plan.get("idempotency_key"),
                        "expires_at": plan.get("expires_at"),
                        "risk_tier": plan.get("risk_tier"),
                    }
                )
                self._update(row)
                self.add_event(row["id"], "proposed", {"refreshed": True})
                return row

        return self.create_proposed(plan)

    def create_proposed(self, plan: dict[str, Any]) -> dict[str, Any]:
        incident_id = str(plan.get("incident_id") or "")
        plan_spec = plan.get("plan_spec") or (plan.get("details") or {}).get("plan_spec")
        row = {
            "id": str(uuid.uuid4()),
            "incident_id": incident_id,
            "report_id": plan.get("report_id"),
            "candidate_node": plan.get("candidate_node"),
            "fault_class": plan.get("fault_class"),
            "action": plan.get("action"),
            "depth": plan.get("depth"),
            "summary": plan.get("summary"),
            "details": plan.get("details") or {},
            "plan_spec": plan_spec,
            "before_fingerprint": plan.get("before_fingerprint"),
            "idempotency_key": plan.get("idempotency_key"),
            "expires_at": plan.get("expires_at"),
            "risk_tier": plan.get("risk_tier"),
            "status": "proposed",
            "created_at": _iso(),
            "decided_at": None,
            "decided_by": None,
            "apply_result": None,
            "claimed_by": None,
            "claimed_at": None,
            "lease_until": None,
            "events": [],
        }
        if self._dsn:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO remediation_plans (
                            id, incident_id, report_id, candidate_node, fault_class,
                            action, depth, summary, details, plan_spec, before_fingerprint,
                            idempotency_key, expires_at, risk_tier, status, created_at
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s
                        )
                        """,
                        (
                            row["id"],
                            row["incident_id"],
                            row["report_id"],
                            row["candidate_node"],
                            row["fault_class"],
                            row["action"],
                            row["depth"],
                            row["summary"],
                            json.dumps(row["details"]),
                            json.dumps(row["plan_spec"]) if row["plan_spec"] is not None else None,
                            row["before_fingerprint"],
                            row["idempotency_key"],
                            row["expires_at"],
                            row["risk_tier"],
                            row["status"],
                            row["created_at"],
                        ),
                    )
                self.add_event(
                    row["id"],
                    "proposed",
                    {
                        "source": (row["details"] or {}).get("source"),
                        "regen_of_plan_id": (row["details"] or {}).get("regen_of_plan_id"),
                        "parent_plan_id": (row["details"] or {}).get("parent_plan_id"),
                        "nim_prompt_source": (row["details"] or {}).get("nim_prompt_source"),
                    },
                )
                return row
            except Exception as exc:
                if not self._allow_file:
                    raise RuntimeError("remediation plan insert failed") from exc
                logger.warning("pg create proposed failed, development file fallback: %s", exc)
                self._dsn = None
        rows = self._load_file()
        rows.insert(0, row)
        self._save_file(rows)
        self.add_event(
            row["id"],
            "proposed",
            {
                "source": (row["details"] or {}).get("source"),
                "regen_of_plan_id": (row["details"] or {}).get("regen_of_plan_id"),
                "parent_plan_id": (row["details"] or {}).get("parent_plan_id"),
                "nim_prompt_source": (row["details"] or {}).get("nim_prompt_source"),
            },
        )
        return row

    def _update(self, row: dict[str, Any]) -> None:
        if self._dsn:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE remediation_plans SET
                            candidate_node=%s, fault_class=%s, action=%s, depth=%s,
                            summary=%s, details=%s::jsonb, plan_spec=%s::jsonb,
                            before_fingerprint=%s, idempotency_key=%s, expires_at=%s,
                            risk_tier=%s, status=%s, decided_at=%s, decided_by=%s,
                            apply_result=%s::jsonb, report_id=%s,
                            claimed_by=%s, claimed_at=%s, lease_until=%s
                        WHERE id=%s
                        """,
                        (
                            row.get("candidate_node"),
                            row.get("fault_class"),
                            row.get("action"),
                            row.get("depth"),
                            row.get("summary"),
                            json.dumps(row.get("details") or {}),
                            json.dumps(row.get("plan_spec")) if row.get("plan_spec") is not None else None,
                            row.get("before_fingerprint"),
                            row.get("idempotency_key"),
                            row.get("expires_at"),
                            row.get("risk_tier"),
                            row.get("status"),
                            row.get("decided_at"),
                            row.get("decided_by"),
                            json.dumps(row.get("apply_result")) if row.get("apply_result") is not None else None,
                            row.get("report_id"),
                            row.get("claimed_by"),
                            row.get("claimed_at"),
                            row.get("lease_until"),
                            row["id"],
                        ),
                    )
                return
            except Exception as exc:
                logger.warning("pg update failed: %s", exc)
                if not self._allow_file:
                    raise RuntimeError("remediation plan update failed") from exc
                self._dsn = None
        rows = self._load_file()
        for i, existing in enumerate(rows):
            if existing.get("id") == row["id"]:
                rows[i] = {**existing, **row}
                break
        self._save_file(rows)

    def get(self, plan_id: str) -> dict[str, Any] | None:
        if self._dsn:
            try:
                with self._connect() as conn:
                    cur = conn.execute(
                        """
                        SELECT id, incident_id, report_id, candidate_node, fault_class,
                               action, depth, summary, details, status, created_at,
                               decided_at, decided_by, apply_result, plan_spec,
                               before_fingerprint, idempotency_key, expires_at, risk_tier,
                               claimed_by, claimed_at, lease_until
                        FROM remediation_plans WHERE id=%s
                        """,
                        (plan_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    plan = self._row_to_dict(row)
                    ev = conn.execute(
                        """
                        SELECT id, plan_id, event_type, payload, created_at
                        FROM remediation_events WHERE plan_id=%s ORDER BY created_at
                        """,
                        (plan_id,),
                    ).fetchall()
                    plan["events"] = [
                        {
                            "id": e[0],
                            "plan_id": e[1],
                            "event_type": e[2],
                            "payload": e[3] if isinstance(e[3], dict) else json.loads(e[3] or "{}"),
                            "created_at": str(e[4]),
                        }
                        for e in ev
                    ]
                    return plan
            except Exception as exc:
                logger.warning("pg get failed: %s", exc)
                if not self._allow_file:
                    raise RuntimeError("remediation plan read failed") from exc
                self._dsn = None
        for row in self._load_file():
            if row.get("id") == plan_id:
                return row
        return None

    def _parse_json(self, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    def _row_to_dict(self, row: tuple) -> dict[str, Any]:
        return {
            "id": row[0],
            "incident_id": row[1],
            "report_id": row[2],
            "candidate_node": row[3],
            "fault_class": row[4],
            "action": row[5],
            "depth": row[6],
            "summary": row[7],
            "details": self._parse_json(row[8]) or {},
            "status": row[9],
            "created_at": str(row[10]) if row[10] else None,
            "decided_at": str(row[11]) if row[11] else None,
            "decided_by": row[12],
            "apply_result": self._parse_json(row[13]),
            "plan_spec": self._parse_json(row[14]) if len(row) > 14 else None,
            "before_fingerprint": row[15] if len(row) > 15 else None,
            "idempotency_key": row[16] if len(row) > 16 else None,
            "expires_at": str(row[17]) if len(row) > 17 and row[17] else None,
            "risk_tier": row[18] if len(row) > 18 else None,
            "claimed_by": row[19] if len(row) > 19 else None,
            "claimed_at": str(row[20]) if len(row) > 20 and row[20] else None,
            "lease_until": str(row[21]) if len(row) > 21 and row[21] else None,
        }

    def list_plans(
        self,
        *,
        status: str | None = None,
        fault_class: str | None = None,
        incident_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if self._dsn:
            try:
                clauses = ["1=1"]
                params: list[Any] = []
                if status:
                    clauses.append("status=%s")
                    params.append(status)
                if fault_class:
                    clauses.append("fault_class=%s")
                    params.append(fault_class)
                if incident_id:
                    clauses.append("incident_id=%s")
                    params.append(incident_id)
                params.append(limit)
                sql = f"""
                    SELECT id, incident_id, report_id, candidate_node, fault_class,
                           action, depth, summary, details, status, created_at,
                           decided_at, decided_by, apply_result, plan_spec,
                           before_fingerprint, idempotency_key, expires_at, risk_tier,
                           claimed_by, claimed_at, lease_until
                    FROM remediation_plans
                    WHERE {' AND '.join(clauses)}
                    ORDER BY created_at DESC
                    LIMIT %s
                """
                with self._connect() as conn:
                    rows = conn.execute(sql, params).fetchall()
                return [self._row_to_dict(r) for r in rows]
            except Exception as exc:
                logger.warning("pg list failed: %s", exc)
                if not self._allow_file:
                    raise RuntimeError("remediation plan list failed") from exc
                self._dsn = None
        rows = self._load_file()
        out = []
        for row in rows:
            if status and row.get("status") != status:
                continue
            if fault_class and row.get("fault_class") != fault_class:
                continue
            if incident_id and row.get("incident_id") != incident_id:
                continue
            out.append(row)
        return out[:limit]

    def set_status(
        self,
        plan_id: str,
        status: str,
        *,
        decided_by: str | None = None,
        apply_result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row = self.get(plan_id)
        if not row:
            return None
        row["status"] = status
        if status in ("approved", "rejected", "succeeded", "failed", "rolled_back", "expired"):
            row["decided_at"] = _iso()
            if decided_by:
                row["decided_by"] = decided_by
            elif not row.get("decided_by"):
                row["decided_by"] = "operator"
        if apply_result is not None:
            row["apply_result"] = apply_result
        self._update(row)
        self.add_event(plan_id, status, apply_result or {"decided_by": row.get("decided_by")})
        return self.get(plan_id)

    def claim_next(self, worker_id: str, *, lease_seconds: int = 300) -> dict[str, Any] | None:
        now = _utcnow()
        lease_until = now + timedelta(seconds=lease_seconds)
        if self._dsn:
            try:
                import psycopg

                with psycopg.connect(self._dsn) as conn:
                    conn.autocommit = False
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT id FROM remediation_plans
                            WHERE status = 'approved'
                               OR (status IN ('claimed', 'executing', 'observing')
                                   AND (lease_until IS NULL OR lease_until < NOW()))
                            ORDER BY created_at ASC
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                            """
                        )
                        found = cur.fetchone()
                        if not found:
                            conn.rollback()
                            return None
                        plan_id = found[0]
                        cur.execute(
                            """
                            UPDATE remediation_plans SET
                                status='claimed', claimed_by=%s, claimed_at=%s, lease_until=%s
                            WHERE id=%s
                            """,
                            (worker_id, now.isoformat(), lease_until.isoformat(), plan_id),
                        )
                        conn.commit()
                self.add_event(plan_id, "claimed", {"worker_id": worker_id, "lease_until": lease_until.isoformat()})
                return self.get(plan_id)
            except Exception as exc:
                logger.warning("claim failed: %s", exc)
                if not self._allow_file:
                    raise RuntimeError("remediation plan claim failed") from exc

        rows = self._load_file()
        for row in rows:
            status = row.get("status")
            lease = row.get("lease_until")
            lease_dt = None
            if lease:
                try:
                    lease_dt = datetime.fromisoformat(str(lease).replace("Z", "+00:00"))
                except ValueError:
                    lease_dt = None
            reclaim = status in ("claimed", "executing", "observing") and (lease_dt is None or lease_dt < now)
            if status == "approved" or reclaim:
                row["status"] = "claimed"
                row["claimed_by"] = worker_id
                row["claimed_at"] = _iso(now)
                row["lease_until"] = _iso(lease_until)
                self._save_file(rows)
                self.add_event(row["id"], "claimed", {"worker_id": worker_id})
                return row
        return None

    def heartbeat(self, plan_id: str, worker_id: str, *, lease_seconds: int = 300) -> None:
        lease_until = _utcnow() + timedelta(seconds=lease_seconds)
        if self._dsn:
            with self._connect() as conn:
                updated = conn.execute(
                    """
                    UPDATE remediation_plans
                    SET lease_until=%s
                    WHERE id=%s AND claimed_by=%s
                      AND status IN ('claimed', 'executing', 'observing')
                    RETURNING id
                    """,
                    (lease_until.isoformat(), plan_id, worker_id),
                ).fetchone()
            if not updated:
                raise RuntimeError(f"worker {worker_id} no longer owns plan {plan_id}")
            return
        row = self.get(plan_id)
        if not row or row.get("claimed_by") != worker_id:
            raise RuntimeError(f"worker {worker_id} no longer owns plan {plan_id}")
        row["lease_until"] = _iso(lease_until)
        self._update(row)

    def owns_lease(self, plan_id: str, worker_id: str) -> bool:
        row = self.get(plan_id)
        if not row or row.get("claimed_by") != worker_id:
            return False
        raw = row.get("lease_until")
        if not raw:
            return False
        try:
            lease = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return False
        return lease > _utcnow() and row.get("status") in {
            "claimed",
            "executing",
            "observing",
        }

    def set_status_if_owned(
        self,
        plan_id: str,
        status: str,
        worker_id: str,
        *,
        apply_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._dsn:
            decided_at = _iso() if status in TERMINAL else None
            with self._connect() as conn:
                updated = conn.execute(
                    """
                    UPDATE remediation_plans
                    SET status=%s,
                        decided_at=COALESCE(%s, decided_at),
                        apply_result=COALESCE(%s::jsonb, apply_result)
                    WHERE id=%s AND claimed_by=%s
                      AND lease_until > NOW()
                    RETURNING id
                    """,
                    (
                        status,
                        decided_at,
                        json.dumps(apply_result) if apply_result is not None else None,
                        plan_id,
                        worker_id,
                    ),
                ).fetchone()
            if not updated:
                raise RuntimeError(f"worker {worker_id} no longer owns plan {plan_id}")
            self.add_event(plan_id, status, apply_result or {"worker_id": worker_id})
            row = self.get(plan_id)
            if not row:
                raise RuntimeError(f"plan {plan_id} disappeared during status update")
            return row
        row = self.get(plan_id)
        if not row or not self.owns_lease(plan_id, worker_id):
            raise RuntimeError(f"worker {worker_id} no longer owns plan {plan_id}")
        row["status"] = status
        if status in TERMINAL:
            row["decided_at"] = _iso()
        if apply_result is not None:
            row["apply_result"] = apply_result
        self._update(row)
        self.add_event(plan_id, status, apply_result or {"worker_id": worker_id})
        updated = self.get(plan_id)
        if not updated:
            raise RuntimeError(f"plan {plan_id} disappeared during status update")
        return updated

    def stats(self) -> dict[str, Any]:
        rows = self.list_plans(limit=500)
        by_status: dict[str, int] = {}
        by_class: dict[str, int] = {}
        for row in rows:
            by_status[row.get("status") or "unknown"] = by_status.get(row.get("status") or "unknown", 0) + 1
            by_class[row.get("fault_class") or "unknown"] = by_class.get(row.get("fault_class") or "unknown", 0) + 1
        return {
            "total": len(rows),
            "awaiting_review": by_status.get("proposed", 0),
            "approved_pending": by_status.get("approved", 0),
            "by_status": by_status,
            "by_fault_class": by_class,
        }
