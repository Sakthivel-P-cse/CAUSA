"""Restricted PostgreSQL state reader and RCA report writer."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from contracts import (
    AnomalyOutput,
    Baseline,
    IncidentTelemetry,
    RankedResult,
    Role,
    Thresholds,
)

logger = logging.getLogger(__name__)


def operator_root_cause_summary(
    *,
    top_node: str | None,
    alert_name: str | None,
    alerted_service: str | None,
    role: Role | None,
    abstain_reason: str | None,
) -> str:
    """Short operator-facing summary; candidate.explanation stays technical."""
    if top_node is None:
        return f"RCA abstained: {abstain_reason or 'no candidates'}"
    alert = alert_name or "the alert"
    role_label = (role.value if role is not None else "origin").replace("_", " ")
    if alerted_service:
        return (
            f"{top_node} is the top {role_label} candidate for {alert} "
            f"on {alerted_service}"
        )
    return f"{top_node} is the top {role_label} candidate for {alert}"


def _libpq_kv(key: str, value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"{key}='{escaped}'"


def dsn_from_env() -> str:
    """Build the engine DSN and reject accidental use of an owner/eval role."""
    user = os.getenv("PGUSER", "rca_engine")
    if user != "rca_engine":
        raise RuntimeError("Stage-2 must connect as the restricted rca_engine role")
    pairs = {
        "host": os.getenv("PGHOST", "postgres.postgres.svc.cluster.local"),
        "port": os.getenv("PGPORT", "5432"),
        "dbname": os.getenv("PGDATABASE", "causa"),
        "user": user,
        "password": os.getenv("PGPASSWORD", "rca_engine"),
    }
    return " ".join(_libpq_kv(key, value) for key, value in pairs.items())


@dataclass(frozen=True)
class IncidentState:
    incident_id: str
    alert_name: str
    alerted_service: str | None
    namespace: str | None
    node: str | None
    severity: str | None
    window_start: datetime | None
    window_end: datetime | None
    trace_id: str | None
    telemetry_refs: dict[str, Any]
    status: str
    triggered_at: datetime
    resolved_at: datetime | None


class RCAStore:
    """Only accesses incidents, anomaly_baseline, and rca_reports."""

    def __init__(
        self,
        *,
        connect_factory: Callable[[], Any] | None = None,
    ):
        self._connect_factory = connect_factory
        self._dsn = "" if connect_factory is not None else dsn_from_env()

    def _connect(self):
        if self._connect_factory is not None:
            return self._connect_factory()
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "psycopg not installed — `pip install '.[trigger]'` to run Stage-2"
            ) from exc
        return psycopg.connect(self._dsn, autocommit=True)

    def check_available(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1")

    def load_incident(self, incident_id: str) -> IncidentState:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT incident_id, alert_name, alerted_service, namespace, node,
                       severity, window_start, window_end, trace_id, telemetry_refs,
                       status, triggered_at, resolved_at
                FROM incidents
                WHERE incident_id = %s
                """,
                (incident_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"incident not found: {incident_id}")
        refs = row[9] or {}
        if isinstance(refs, (str, bytes, bytearray)):
            refs = json.loads(refs)
        return IncidentState(*row[:9], refs, *row[10:])

    def load_baselines(self) -> dict[tuple[str, str], Baseline]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT node, metric, median, mad, n
                FROM anomaly_baseline
                """,
            ).fetchall()
        return {
            (row[0], row[1]): Baseline(
                median=float(row[2]), mad=float(row[3]), n=int(row[4])
            )
            for row in rows
        }

    def insert_report(
        self,
        incident: IncidentState,
        telemetry: IncidentTelemetry,
        anomalies: list[AnomalyOutput],
        result: RankedResult,
        thresholds: Thresholds | None = None,
    ) -> None:
        thresholds = thresholds or Thresholds()
        top = result.candidates[0] if result.candidates else None
        anomaly_scores = {
            output.node: output.anomaly_score for output in anomalies
        }

        # A node we have CONCLUDED is an effect is not a candidate cause. Both
        # of these columns previously received the entire ranked list, so
        # `origin_candidates` contained known victims and `blast_radius` was a
        # copy of it rather than a radius. Consumers keying on the column names
        # — Dev C's classifier among them — got the wrong set.
        effects = {Role.VICTIM, Role.VICTIM_OF_INFRA}
        origin_candidates = [
            candidate.to_dict()
            for candidate in result.candidates
            if candidate.role not in effects
        ]
        amplifiers = [
            candidate.node_id
            for candidate in result.candidates
            if candidate.role == Role.AMPLIFIER
        ]
        origins = {
            candidate.node_id
            for candidate in result.candidates
            if candidate.role == Role.ORIGIN
        }
        # What the incident actually reached: nodes that genuinely deviated,
        # minus the cause itself. Connectivity-expanded neighbours sitting below
        # θ_fire were never impacted and do not belong in a blast radius.
        blast_radius = sorted(
            node
            for node, score in anomaly_scores.items()
            if score >= thresholds.theta_fire and node not in origins
        )
        evidence = [
            {
                "node": candidate.node_id,
                "score": candidate.score,
                "signals": candidate.signals.to_dict(),
                "dev_vector": candidate.dev_vector,
                "explanation": candidate.explanation,
            }
            for candidate in result.candidates
        ]
        # Timeline is metric-onset order: first_deviation from anomaly series.
        # Enrich with score/role so the UI does not invent fake uncertainty chips.
        role_by_node = {
            candidate.node_id: (
                candidate.role.value if hasattr(candidate.role, "value") else str(candidate.role)
            )
            for candidate in result.candidates
        }
        onset_events = [
            output for output in anomalies if output.first_deviation is not None
        ]
        onset_events.sort(key=lambda output: (output.first_deviation, output.node))
        origin_ts = onset_events[0].first_deviation if onset_events else None
        timeline = []
        for output in onset_events:
            offset_s = (
                (output.first_deviation - origin_ts).total_seconds()
                if origin_ts is not None
                else None
            )
            role = role_by_node.get(output.node)
            timeline.append(
                {
                    "timestamp": output.first_deviation.isoformat(),
                    "service": output.node,
                    "event": "first deviation",
                    "anomaly_score": round(float(output.anomaly_score), 4),
                    "role": role,
                    "offset_seconds": round(offset_s, 1) if offset_s is not None else None,
                    "ordering_source": "metric_onset",
                    "key_moment": role in {"origin", "amplifier"}
                    or float(output.anomaly_score) >= thresholds.theta_fire,
                }
            )
        summary = operator_root_cause_summary(
            top_node=top.node_id if top is not None else None,
            alert_name=incident.alert_name,
            alerted_service=incident.alerted_service,
            role=top.role if top is not None else None,
            abstain_reason=result.abstain_reason,
        )

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO rca_reports (
                    triggered_at, alert_name, alerted_service, root_cause_service,
                    failure_type, incident_id, root_cause_summary,
                    origin_candidates, amplifier_nodes, blast_radius,
                    anomaly_scores, incident_scope, inference_evidence,
                    confidence_score, evidence, timeline, trace_id, resolved_at
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                ON CONFLICT (incident_id) WHERE incident_id IS NOT NULL
                DO UPDATE SET
                    triggered_at = EXCLUDED.triggered_at,
                    alert_name = EXCLUDED.alert_name,
                    alerted_service = EXCLUDED.alerted_service,
                    root_cause_service = EXCLUDED.root_cause_service,
                    failure_type = EXCLUDED.failure_type,
                    root_cause_summary = EXCLUDED.root_cause_summary,
                    origin_candidates = EXCLUDED.origin_candidates,
                    amplifier_nodes = EXCLUDED.amplifier_nodes,
                    blast_radius = EXCLUDED.blast_radius,
                    anomaly_scores = EXCLUDED.anomaly_scores,
                    incident_scope = EXCLUDED.incident_scope,
                    inference_evidence = EXCLUDED.inference_evidence,
                    confidence_score = EXCLUDED.confidence_score,
                    evidence = EXCLUDED.evidence,
                    timeline = EXCLUDED.timeline,
                    trace_id = EXCLUDED.trace_id,
                    resolved_at = EXCLUDED.resolved_at
                WHERE rca_reports.root_cause_summary ILIKE '%%missing_anomaly_baselines%%'
                """,
                (
                    incident.triggered_at,
                    incident.alert_name,
                    incident.alerted_service,
                    top.node_id if top else None,
                    result.fault_class,
                    incident.incident_id,
                    summary,
                    json.dumps(origin_candidates, allow_nan=False),
                    json.dumps(amplifiers, allow_nan=False),
                    json.dumps(blast_radius),
                    json.dumps(anomaly_scores, allow_nan=False),
                    json.dumps(
                        {
                            "namespace": incident.namespace,
                            "alerted_service": incident.alerted_service,
                            "nodes": sorted(anomaly_scores),
                        }
                    ),
                    json.dumps(result.to_dict(), allow_nan=False),
                    top.confidence if top else 0.0,
                    json.dumps(evidence, allow_nan=False),
                    json.dumps(timeline),
                    telemetry.trace_id or incident.trace_id,
                    incident.resolved_at,
                ),
            )
            if cursor.rowcount == 0:
                logger.info(
                    "RCA report for incident %s already exists; skipped duplicate insert",
                    incident.incident_id,
                )
                return
        report_payload = {
            "incident_id": incident.incident_id,
            "id": incident.incident_id,
            "alert_name": incident.alert_name,
            "alerted_service": incident.alerted_service,
            "root_cause_service": top.node_id if top else None,
            "failure_type": (
                result.fault_class.value
                if hasattr(result.fault_class, "value")
                else result.fault_class
            ),
            "root_cause_summary": summary,
            "confidence_score": top.confidence if top else 0.0,
            "origin_candidates": origin_candidates,
            "timeline": timeline,
            "blast_radius": blast_radius,
            "trace_id": telemetry.trace_id or incident.trace_id,
        }
        try:
            from remediation.auto import auto_remediate_from_report

            rem = auto_remediate_from_report(report_payload)
            logger.info("auto remediation for %s: %s", incident.incident_id, rem)
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto remediation failed for %s: %s", incident.incident_id, exc)
