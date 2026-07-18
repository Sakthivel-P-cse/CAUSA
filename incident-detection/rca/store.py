"""Restricted PostgreSQL state reader and RCA report writer."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from contracts import AnomalyOutput, Baseline, IncidentTelemetry, RankedResult, Role


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
    ) -> None:
        candidates = [candidate.to_dict() for candidate in result.candidates]
        top = result.candidates[0] if result.candidates else None
        amplifiers = [
            candidate.node_id
            for candidate in result.candidates
            if candidate.role == Role.AMPLIFIER
        ]
        anomaly_scores = {
            output.node: output.anomaly_score for output in anomalies
        }
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
        timeline = [
            {
                "timestamp": output.first_deviation.isoformat(),
                "service": output.node,
                "event": "first deviation",
            }
            for output in anomalies
            if output.first_deviation is not None
        ]
        timeline.sort(key=lambda event: (event["timestamp"], event["service"]))
        summary = (
            f"Top root-cause candidate: {top.node_id}. {top.explanation}"
            if top is not None
            else f"RCA abstained: {result.abstain_reason or 'no candidates'}"
        )

        with self._connect() as conn:
            conn.execute(
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
                """,
                (
                    incident.triggered_at,
                    incident.alert_name,
                    incident.alerted_service,
                    top.node_id if top else None,
                    result.fault_class,
                    incident.incident_id,
                    summary,
                    json.dumps(candidates, allow_nan=False),
                    json.dumps(amplifiers, allow_nan=False),
                    json.dumps([candidate.node_id for candidate in result.candidates]),
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
