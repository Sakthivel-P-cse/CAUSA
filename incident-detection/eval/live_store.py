"""Postgres read client as causa_eval — join rca_reports to ground truth."""

from __future__ import annotations

import json
import os
from typing import Any, Callable


def _libpq_kv(key: str, value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"{key}='{escaped}'"


def dsn_from_env() -> str:
    explicit = os.getenv("CAUSA_EVAL_DSN")
    if explicit:
        return explicit
    pairs = {
        "host": os.getenv("PGHOST", "postgres.postgres.svc.cluster.local"),
        "port": os.getenv("PGPORT", "5432"),
        "dbname": os.getenv("PGDATABASE", "causa"),
        "user": os.getenv("PGUSER", "causa_eval"),
        "password": os.getenv("PGPASSWORD", "causa_eval"),
    }
    return " ".join(_libpq_kv(k, v) for k, v in pairs.items())


_JOIN_SQL = """
SELECT r.incident_id, r.root_cause_service, r.failure_type, r.confidence_score,
       r.origin_candidates, g.true_root_node, g.fault_class, r.alert_name,
       g.topology_id, g.labeler, g.verified_at
FROM rca_reports r
JOIN groundtruth.ground_truth g ON r.incident_id = g.incident_id
"""


def _parse_candidates(raw: Any) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, (str, bytes, bytearray)):
        raw = json.loads(raw)
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


class LiveEvalStore:
    """Read-only PG client intended for the causa_eval role."""

    def __init__(
        self,
        dsn: str | None = None,
        *,
        connect_factory: Callable[[], Any] | None = None,
    ):
        self._connect_factory = connect_factory
        self._dsn = dsn or ("" if connect_factory is not None else dsn_from_env())

    def _connect(self):
        if self._connect_factory is not None:
            return self._connect_factory()
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "psycopg not installed — `pip install '.[trigger]'` for live eval"
            ) from exc
        return psycopg.connect(self._dsn, autocommit=True)

    def join_reports_groundtruth(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(_JOIN_SQL).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "incident_id": row[0],
                    "root_cause_service": row[1],
                    "failure_type": row[2],
                    "confidence_score": float(row[3]) if row[3] is not None else None,
                    "origin_candidates": _parse_candidates(row[4]),
                    "true_root_node": row[5],
                    "fault_class": row[6],
                    "alert_name": row[7],
                    "topology_id": row[8],
                    "labeler": row[9],
                    "verified_at": row[10].isoformat() if row[10] else None,
                }
            )
        return out
