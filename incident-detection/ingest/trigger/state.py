"""
state.py — Postgres incident-state writer (PRD2 §9).

Dev A owns incident WINDOW state (open/resolve) + the ground-truth store; the
external Stage-2 engine owns rca_reports (conclusions). Schema is created by
helm/postgres init.sql: `incidents`, `groundtruth.ground_truth`, `anomaly_baseline`.

psycopg is imported lazily so this module (and the rest of `ingest`) imports
cleanly without the optional `trigger` extra installed.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from contracts import Baseline, Incident

logger = logging.getLogger(__name__)


def _libpq_kv(key: str, value: str) -> str:
    """
    One `key='value'` pair for a libpq keyword/value DSN. Always single-quotes
    and escapes `\\` and `'`, so a password containing a space, quote, or
    backslash can't break the connection string (conninfo injection).
    """
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"{key}='{escaped}'"


def dsn_from_env() -> str:
    """Build a libpq DSN from PG* env vars (defaults match helm/postgres)."""
    pairs = {
        "host": os.getenv("PGHOST", "postgres.postgres.svc.cluster.local"),
        "port": os.getenv("PGPORT", "5432"),
        "dbname": os.getenv("PGDATABASE", "causa"),
        "user": os.getenv("PGUSER", "causa"),
        "password": os.getenv("PGPASSWORD", "causa"),
    }
    return " ".join(_libpq_kv(k, v) for k, v in pairs.items())


class IncidentStore:
    """Thin writer for the ingestion-owned incident/ground-truth tables."""

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or dsn_from_env()

    def _connect(self):
        try:
            import psycopg  # lazy: only needed when the service actually runs
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "psycopg not installed — `pip install '.[trigger]'` to run the trigger service"
            ) from exc
        return psycopg.connect(self._dsn, autocommit=True)

    def check_available(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1")

    def enqueue_webhook(self, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO trigger_webhooks (payload) VALUES (%s::jsonb)", (json.dumps(payload),))

    @contextmanager
    def claim_webhook(self):
        # Keep the row lock until processing finishes; a crashed worker releases it.
        with self._connect() as conn:
            with conn.transaction():
                # Preserve firing/resolved order across concurrent worker processes.
                if not conn.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtext('causa.trigger_webhooks'))"
                ).fetchone()[0]:
                    yield None
                    return
                row = conn.execute(
                    "SELECT id, payload FROM trigger_webhooks ORDER BY id "
                    "FOR UPDATE SKIP LOCKED LIMIT 1"
                ).fetchone()
                if not row:
                    yield None
                    return
                payload = row[1]
                if isinstance(payload, (str, bytes, bytearray)):
                    payload = json.loads(payload)
                yield payload
                conn.execute("DELETE FROM trigger_webhooks WHERE id=%s", (row[0],))

    # ── incidents (window state) ──────────────────────────────────────

    def open_incident(
        self,
        *,
        incident_id: str,
        alert_name: str,
        alerted_service: str | None,
        namespace: str | None,
        node: str | None,
        severity: str | None,
        window_start: datetime,
        window_end: datetime,
        trace_id: str | None,
        telemetry_refs: dict[str, Any] | None = None,
        snapshot_status: str | None = None,
    ) -> None:
        """Insert (or no-op on re-fire) an open incident row."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO incidents (
                    incident_id, alert_name, alerted_service, namespace, node,
                    severity, window_start, window_end, trace_id, telemetry_refs,
                    status, snapshot_status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s)
                ON CONFLICT (incident_id) DO NOTHING
                """,
                (
                    incident_id, alert_name, alerted_service, namespace, node,
                    severity, window_start, window_end, trace_id,
                    json.dumps(telemetry_refs or {}),
                    snapshot_status,
                ),
            )
        logger.info("Opened incident %s (%s / %s)", incident_id, alert_name, alerted_service)

    def resolve_incident(
        self, incident_id: str, resolved_at: datetime, *,
        legacy_id: str | None = None, starts_at: datetime | None = None,
    ) -> bool:
        """
        Mark an incident resolved (from an Alertmanager send_resolved POST).

        Returns False when no matching open incident was found — a resolve that
        arrived before (or without) its open. The caller logs it rather than
        letting the resolve vanish silently.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE incidents SET status='resolved', resolved_at=%s "
                "WHERE status<>'resolved' AND (incident_id=%s OR "
                "(incident_id=%s AND window_end=%s))",
                (resolved_at, incident_id, legacy_id,
                 starts_at + timedelta(minutes=1) if starts_at else None),
            )
            matched = cur.rowcount
        if matched:
            logger.info("Resolved incident %s", incident_id)
            return True
        logger.warning(
            "Resolve for %s matched no open incident (resolve-before-open?)", incident_id
        )
        return False

    # ── ground truth (written by the chaos harness, NOT the engine path) ──

    def write_ground_truth(
        self,
        incident: Incident,
        *,
        topology_id: str,
        labeler: str,
        verified_at: datetime | None,
        label_notes: str | None = None,
    ) -> None:
        """Persist a chaos run's known root into the role-isolated schema.

        ``verified_at`` is None when the run never confirmed the injection
        (``--allow-unverified``); the column is nullable so eval can filter on
        it rather than trusting every row equally.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO groundtruth.ground_truth (
                    incident_id, true_root_node, fault_class, inject_start, inject_end,
                    topology_id, labeler, verified_at, label_notes
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (incident_id) DO UPDATE SET
                    true_root_node = EXCLUDED.true_root_node,
                    fault_class    = EXCLUDED.fault_class,
                    inject_start   = EXCLUDED.inject_start,
                    inject_end     = EXCLUDED.inject_end,
                    topology_id    = EXCLUDED.topology_id,
                    labeler        = EXCLUDED.labeler,
                    verified_at    = EXCLUDED.verified_at,
                    label_notes    = EXCLUDED.label_notes
                """,
                (
                    incident.incident_id, incident.true_root_node,
                    incident.fault_class, incident.inject_start, incident.inject_end,
                    topology_id, labeler, verified_at, label_notes,
                ),
            )

    # ── Stage-2 forward outbox (durable, so an outage can't drop it) ──

    def enqueue_outbox(self, payload: dict[str, Any], last_error: str | None = None) -> None:
        """Persist a webhook that could not be forwarded to Stage-2 for retry."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stage2_outbox (payload, attempts, last_error)
                VALUES (%s, 1, %s)
                """,
                (json.dumps(payload), last_error),
            )
        logger.warning("Stage-2 forward buffered to outbox (last_error=%s)", last_error)

    def pending_outbox(self, limit: int = 50, max_attempts: int = 20) -> list[dict[str, Any]]:
        """Fetch not-yet-delivered outbox rows still under the attempt ceiling."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                SELECT id, payload FROM stage2_outbox
                WHERE sent_at IS NULL AND attempts < %s
                ORDER BY id ASC LIMIT %s
                """,
                (max_attempts, limit),
            )
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = row[1]
            if isinstance(payload, (str, bytes, bytearray)):
                payload = json.loads(payload)
            out.append({"id": row[0], "payload": payload})
        return out

    def mark_outbox_sent(self, outbox_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE stage2_outbox SET sent_at = NOW() WHERE id = %s", (outbox_id,)
            )

    def bump_outbox_attempt(self, outbox_id: int, last_error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE stage2_outbox SET attempts = attempts + 1, last_error = %s "
                "WHERE id = %s",
                (last_error, outbox_id),
            )

    # ── baseline store (optional shared copy) ─────────────────────────

    def upsert_baselines(self, baselines: dict[tuple[str, str], Baseline]) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                for (node, metric), b in baselines.items():
                    cur.execute(
                        """
                        INSERT INTO anomaly_baseline (node, metric, median, mad, n, captured_at)
                        VALUES (%s,%s,%s,%s,%s, NOW())
                        ON CONFLICT (node, metric) DO UPDATE SET
                            median=EXCLUDED.median, mad=EXCLUDED.mad,
                            n=EXCLUDED.n, captured_at=NOW()
                        """,
                        (node, metric, b.median, b.mad, b.n),
                    )
