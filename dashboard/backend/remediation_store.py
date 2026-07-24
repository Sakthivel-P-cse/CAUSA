"""Persistence for remediation plans (Postgres preferred, JSON file fallback)."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dashboard.backend.db import dsn_from_env, outputs_dir

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utcnow()).isoformat()


class RemediationStore:
    def __init__(self) -> None:
        self._dsn = dsn_from_env()
        self._file = Path(
            os.getenv(
                "CAUSA_REMEDIATION_STORE",
                str(outputs_dir() / "c" / "remediation_plans.json"),
            )
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
        sql = """
        CREATE TABLE IF NOT EXISTS remediation_plans (
            id              TEXT PRIMARY KEY,
            incident_id     TEXT NOT NULL,
            report_id       TEXT,
            candidate_node  TEXT,
            fault_class     TEXT,
            action          TEXT,
            depth           TEXT,
            summary         TEXT,
            details         JSONB,
            status          TEXT NOT NULL DEFAULT 'proposed',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_at      TIMESTAMPTZ,
            decided_by      TEXT,
            apply_result    JSONB
        );
        CREATE INDEX IF NOT EXISTS idx_remediation_plans_status
            ON remediation_plans (status);
        CREATE INDEX IF NOT EXISTS idx_remediation_plans_incident
            ON remediation_plans (incident_id);
        CREATE TABLE IF NOT EXISTS remediation_events (
            id          TEXT PRIMARY KEY,
            plan_id     TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            payload     JSONB,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_remediation_events_plan
            ON remediation_events (plan_id);
        """
        try:
            with self._connect() as conn:
                conn.execute(sql)
        except Exception as exc:
            logger.warning("remediation schema ensure failed, using file store: %s", exc)
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
        rows = self._load_file()
        for row in rows:
            if row.get("id") == plan_id:
                row.setdefault("events", []).append(event)
                break
        self._save_file(rows)

    def upsert_proposed(self, plan: dict[str, Any]) -> dict[str, Any]:
        incident_id = str(plan.get("incident_id") or "")
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
                }
            )
            self._update(row)
            self.add_event(row["id"], "proposed", {"refreshed": True})
            return row

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
            "status": "proposed",
            "created_at": _iso(),
            "decided_at": None,
            "decided_by": None,
            "apply_result": None,
            "events": [],
        }
        if self._dsn:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO remediation_plans (
                            id, incident_id, report_id, candidate_node, fault_class,
                            action, depth, summary, details, status, created_at
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s
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
                            row["status"],
                            row["created_at"],
                        ),
                    )
                self.add_event(row["id"], "proposed", {"source": (row["details"] or {}).get("source")})
                return row
            except Exception as exc:
                logger.warning("pg upsert failed, file fallback: %s", exc)
                self._dsn = None
        rows = self._load_file()
        rows.insert(0, row)
        self._save_file(rows)
        self.add_event(row["id"], "proposed", {"source": (row["details"] or {}).get("source")})
        return row

    def _update(self, row: dict[str, Any]) -> None:
        if self._dsn:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE remediation_plans SET
                            candidate_node=%s, fault_class=%s, action=%s, depth=%s,
                            summary=%s, details=%s::jsonb, status=%s, decided_at=%s,
                            decided_by=%s, apply_result=%s::jsonb, report_id=%s
                        WHERE id=%s
                        """,
                        (
                            row.get("candidate_node"),
                            row.get("fault_class"),
                            row.get("action"),
                            row.get("depth"),
                            row.get("summary"),
                            json.dumps(row.get("details") or {}),
                            row.get("status"),
                            row.get("decided_at"),
                            row.get("decided_by"),
                            json.dumps(row.get("apply_result")) if row.get("apply_result") is not None else None,
                            row.get("report_id"),
                            row["id"],
                        ),
                    )
                return
            except Exception as exc:
                logger.warning("pg update failed: %s", exc)
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
                               decided_at, decided_by, apply_result
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
                self._dsn = None
        for row in self._load_file():
            if row.get("id") == plan_id:
                return row
        return None

    def _row_to_dict(self, row: tuple) -> dict[str, Any]:
        details = row[8]
        if isinstance(details, str):
            details = json.loads(details)
        apply_result = row[13]
        if isinstance(apply_result, str):
            apply_result = json.loads(apply_result)
        return {
            "id": row[0],
            "incident_id": row[1],
            "report_id": row[2],
            "candidate_node": row[3],
            "fault_class": row[4],
            "action": row[5],
            "depth": row[6],
            "summary": row[7],
            "details": details or {},
            "status": row[9],
            "created_at": str(row[10]) if row[10] else None,
            "decided_at": str(row[11]) if row[11] else None,
            "decided_by": row[12],
            "apply_result": apply_result,
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
                           decided_at, decided_by, apply_result
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
        if status in ("approved", "rejected", "succeeded", "failed", "rolled_back", "applying"):
            row["decided_at"] = _iso()
            row["decided_by"] = decided_by or row.get("decided_by") or "operator"
        if apply_result is not None:
            row["apply_result"] = apply_result
        self._update(row)
        self.add_event(plan_id, status, apply_result or {})
        return self.get(plan_id)

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
            "by_status": by_status,
            "by_fault_class": by_class,
        }
