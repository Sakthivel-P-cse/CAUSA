from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OUTPUTS = _REPO_ROOT / "outputs"


def _libpq_kv(key: str, value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"{key}='{escaped}'"


def dsn_from_env() -> str | None:
    if os.getenv("CAUSA_PG_DSN"):
        return os.getenv("CAUSA_PG_DSN")
    host = os.getenv("PGHOST")
    if not host:
        return None
    pairs = {
        "host": host,
        "port": os.getenv("PGPORT", "5432"),
        "dbname": os.getenv("PGDATABASE", "causa"),
        "user": os.getenv("PGUSER", "rca_engine"),
        "password": os.getenv("PGPASSWORD", "rca_engine"),
    }
    return " ".join(_libpq_kv(k, v) for k, v in pairs.items())


def outputs_dir() -> Path:
    return Path(os.getenv("CAUSA_DASHBOARD_OUTPUTS", str(_DEFAULT_OUTPUTS)))


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class DashboardStore:
    def __init__(self) -> None:
        self._dsn = dsn_from_env()
        self._use_pg = bool(self._dsn)
        self._incidents_cache: list[dict[str, Any]] | None = None
        self._reports_cache: list[dict[str, Any]] | None = None
        self._remediation_cache: dict[str, Any] | None = None

    def close(self) -> None:
        pass

    def _connect(self):
        import psycopg

        return psycopg.connect(self._dsn, autocommit=True)

    def _file_incidents(self) -> list[dict[str, Any]]:
        if self._incidents_cache is not None:
            return self._incidents_cache
        data = _load_json(outputs_dir() / "incidents.json") or []
        self._incidents_cache = data if isinstance(data, list) else []
        return self._incidents_cache

    def _file_reports(self) -> list[dict[str, Any]]:
        if self._reports_cache is not None:
            return self._reports_cache
        data = _load_json(outputs_dir() / "rca_reports.json") or []
        self._reports_cache = data if isinstance(data, list) else []
        return self._reports_cache

    def load_remediation_index(self) -> dict[str, Any]:
        if self._remediation_cache is not None:
            return self._remediation_cache
        out: dict[str, Any] = {}
        for name in ("c/classify_remediation_live.json", "c/nim_classify_remediation.json"):
            data = _load_json(outputs_dir() / name)
            if not data:
                continue
            if isinstance(data, dict) and data.get("incident_id"):
                out[str(data["incident_id"])] = data
            elif isinstance(data, dict) and isinstance(data.get("classifications"), list):
                inc = data.get("incident_id")
                if inc:
                    out[str(inc)] = data
            elif isinstance(data, list):
                for row in data:
                    if isinstance(row, dict) and row.get("incident_id"):
                        out[str(row["incident_id"])] = row
        self._remediation_cache = out
        return out

    def list_incidents_joined(self) -> list[dict[str, Any]]:
        if self._use_pg:
            try:
                return self._pg_list_joined()
            except Exception as exc:
                logger.warning("postgres unavailable, falling back to files: %s", exc)
        return self._file_list_joined()

    def _file_list_joined(self) -> list[dict[str, Any]]:
        incidents = {i["incident_id"]: i for i in self._file_incidents() if i.get("incident_id")}
        reports = self._file_reports()
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for report in reports:
            item = self._normalize_report(report, incidents.get(report.get("incident_id") or ""))
            items.append(item)
            if report.get("incident_id"):
                seen.add(report["incident_id"])
        for inc_id, inc in incidents.items():
            if inc_id in seen:
                continue
            items.append(self._normalize_incident_only(inc))
        items.sort(key=lambda x: x.get("triggered_at") or "", reverse=True)
        return items

    def _pg_list_joined(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  r.id, r.triggered_at, r.alert_name, r.alerted_service,
                  r.root_cause_service, r.failure_type, r.routing, r.incident_id,
                  r.root_cause_summary, r.origin_candidates, r.amplifier_nodes,
                  r.blast_radius, r.anomaly_scores, r.confidence_score, r.evidence,
                  r.timeline, r.trace_id, r.resolved_at, r.created_at,
                  r.feedback_verdict, r.feedback_notes, r.feedback_updated_at,
                  r.first_error_span, r.retry_amplification, r.incident_scope,
                  r.inference_evidence,
                  i.severity, i.status, i.window_start, i.window_end,
                  i.telemetry_refs, i.namespace, i.node
                FROM rca_reports r
                LEFT JOIN incidents i ON i.incident_id = r.incident_id
                ORDER BY r.triggered_at DESC
                """
            ).fetchall()
            cols = [
                "id", "triggered_at", "alert_name", "alerted_service",
                "root_cause_service", "failure_type", "routing", "incident_id",
                "root_cause_summary", "origin_candidates", "amplifier_nodes",
                "blast_radius", "anomaly_scores", "confidence_score", "evidence",
                "timeline", "trace_id", "resolved_at", "created_at",
                "feedback_verdict", "feedback_notes", "feedback_updated_at",
                "first_error_span", "retry_amplification", "incident_scope",
                "inference_evidence",
                "severity", "status", "window_start", "window_end",
                "telemetry_refs", "namespace", "node",
            ]
            reports = [dict(zip(cols, row)) for row in rows]
            orphan = conn.execute(
                """
                SELECT incident_id, alert_name, alerted_service, namespace, node,
                       severity, window_start, window_end, trace_id, telemetry_refs,
                       status, triggered_at, resolved_at
                FROM incidents i
                WHERE NOT EXISTS (
                  SELECT 1 FROM rca_reports r WHERE r.incident_id = i.incident_id
                )
                ORDER BY triggered_at DESC
                """
            ).fetchall()
            ocols = [
                "incident_id", "alert_name", "alerted_service", "namespace", "node",
                "severity", "window_start", "window_end", "trace_id", "telemetry_refs",
                "status", "triggered_at", "resolved_at",
            ]
            orphans = [dict(zip(ocols, row)) for row in orphan]
        items = [self._normalize_report(r, None) for r in reports]
        items.extend(self._normalize_incident_only(o) for o in orphans)
        items.sort(key=lambda x: x.get("triggered_at") or "", reverse=True)
        return items

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        for item in self.list_incidents_joined():
            if str(item.get("id")) == str(report_id) or str(item.get("incident_id")) == str(report_id):
                return item
        return None

    def get_history_item(self, incident_id: str) -> dict[str, Any] | None:
        for item in self.list_incidents_joined():
            if str(item.get("incident_id")) == str(incident_id):
                return item
        return None

    def update_feedback(
        self,
        report_id: str,
        *,
        verdict: str,
        notes: str | None,
    ) -> dict[str, Any]:
        now = datetime.utcnow().isoformat() + "Z"
        if self._use_pg:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE rca_reports
                        SET feedback_verdict = %s,
                            feedback_notes = %s,
                            feedback_updated_at = NOW()
                        WHERE id::text = %s OR incident_id = %s
                        """,
                        (verdict, notes, report_id, report_id),
                    )
            except Exception as exc:
                logger.warning("feedback write failed: %s", exc)
        item = self.get_report(report_id) or {"id": report_id}
        item["feedback"] = {"verdict": verdict, "notes": notes, "updated_at": now}
        return item

    def _normalize_incident_only(self, inc: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": inc.get("incident_id"),
            "incident_id": inc.get("incident_id"),
            "triggered_at": _iso(inc.get("triggered_at")),
            "alert_name": inc.get("alert_name"),
            "alerted_service": inc.get("alerted_service"),
            "root_cause_service": None,
            "failure_type": None,
            "routing": None,
            "severity": (inc.get("severity") or "info").lower(),
            "incident_status": inc.get("status") or "open",
            "confidence_score": None,
            "root_cause_summary": None,
            "trace_id": inc.get("trace_id"),
            "resolved_at": _iso(inc.get("resolved_at")),
            "window_start": _iso(inc.get("window_start")),
            "window_end": _iso(inc.get("window_end")),
            "telemetry_refs": _parse_jsonish(inc.get("telemetry_refs")) or {},
            "namespace": inc.get("namespace"),
            "node": inc.get("node"),
            "origin_candidates": [],
            "amplifier_nodes": [],
            "blast_radius": [],
            "anomaly_scores": {},
            "evidence": [],
            "timeline": [],
            "feedback": None,
            "has_rca": False,
        }

    def _normalize_report(
        self,
        report: dict[str, Any],
        incident: dict[str, Any] | None,
    ) -> dict[str, Any]:
        inc = incident or {}
        anomaly = _parse_jsonish(report.get("anomaly_scores")) or {}
        feedback = None
        if report.get("feedback_verdict"):
            feedback = {
                "verdict": report.get("feedback_verdict"),
                "notes": report.get("feedback_notes"),
                "updated_at": _iso(report.get("feedback_updated_at")),
            }
        refs = _parse_jsonish(
            report.get("telemetry_refs") or inc.get("telemetry_refs")
        ) or {}
        if not isinstance(refs, dict):
            refs = {}
        route = (
            report.get("routing")
            or refs.get("route")
            or None
        )
        runbook_url = refs.get("runbook_url") or refs.get("runbook")
        return {
            "id": str(report.get("id") or report.get("incident_id")),
            "incident_id": report.get("incident_id"),
            "triggered_at": _iso(report.get("triggered_at") or inc.get("triggered_at")),
            "alert_name": report.get("alert_name") or inc.get("alert_name"),
            "alerted_service": report.get("alerted_service") or inc.get("alerted_service"),
            "root_cause_service": report.get("root_cause_service"),
            "failure_type": report.get("failure_type"),
            "routing": route,
            "route": route,
            "runbook_url": runbook_url,
            "layer": refs.get("layer"),
            "slo_target": refs.get("slo_target"),
            "severity": (report.get("severity") or inc.get("severity") or "info").lower(),
            "incident_status": inc.get("status") or ("resolved" if report.get("resolved_at") else "open"),
            "confidence_score": report.get("confidence_score"),
            "root_cause_summary": report.get("root_cause_summary"),
            "trace_id": report.get("trace_id") or inc.get("trace_id"),
            "resolved_at": _iso(report.get("resolved_at") or inc.get("resolved_at")),
            "window_start": _iso(report.get("window_start") or inc.get("window_start")),
            "window_end": _iso(report.get("window_end") or inc.get("window_end")),
            "telemetry_refs": refs,
            "namespace": report.get("namespace") or inc.get("namespace"),
            "node": report.get("node") or inc.get("node"),
            "origin_candidates": _parse_jsonish(report.get("origin_candidates")) or [],
            "amplifier_nodes": _parse_jsonish(report.get("amplifier_nodes")) or [],
            "blast_radius": _parse_jsonish(report.get("blast_radius")) or [],
            "anomaly_scores": anomaly if isinstance(anomaly, dict) else {},
            "evidence": _parse_jsonish(report.get("evidence")) or [],
            "timeline": _parse_jsonish(report.get("timeline")) or [],
            "first_error_span": _parse_jsonish(report.get("first_error_span")),
            "retry_amplification": _parse_jsonish(report.get("retry_amplification")),
            "incident_scope": _parse_jsonish(report.get("incident_scope")),
            "inference_evidence": _parse_jsonish(report.get("inference_evidence")),
            "feedback": feedback,
            "has_rca": True,
            "created_at": _iso(report.get("created_at")),
        }
