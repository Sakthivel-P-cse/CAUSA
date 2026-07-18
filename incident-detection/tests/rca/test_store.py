import json
from datetime import datetime, timedelta, timezone

import pytest

from contracts import (
    AnomalyOutput,
    IncidentTelemetry,
    NodeKind,
    RankedResult,
    Role,
    RootCauseCandidate,
    SignalScores,
)
from rca.store import RCAStore, dsn_from_env


NOW = datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "FROM incidents" in sql:
            return _Cursor(
                [
                    (
                        "inc-abc",
                        "ServiceHighErrorRate",
                        "checkout",
                        "otel-demo",
                        None,
                        "critical",
                        NOW,
                        NOW + timedelta(minutes=11),
                        "trace-1",
                        {"span_count": 3},
                        "open",
                        NOW + timedelta(minutes=10),
                        None,
                    )
                ]
            )
        if "FROM anomaly_baseline" in sql:
            return _Cursor([("checkout", "error_rate", 0.01, 0.002, 60)])
        return _Cursor([])


def test_store_uses_only_engine_tables_and_writes_report():
    connection = _Connection()
    store = RCAStore(
        connect_factory=lambda: connection,
    )
    incident = store.load_incident("inc-abc")
    baselines = store.load_baselines()
    candidate = RootCauseCandidate(
        node_id="checkout",
        node_kind=NodeKind.SERVICE,
        score=0.9,
        confidence=0.8,
        role=Role.ORIGIN,
        signals=SignalScores(T=1.0, D=0.8, P=0.9),
        dev_vector={"error_rate": 0.95},
        explanation="checkout deviated first",
    )
    result = RankedResult(candidates=[candidate], incident_id=incident.incident_id)
    telemetry = IncidentTelemetry(
        incident_id=incident.incident_id,
        alerted_service="checkout",
        window_start=incident.window_start,
        window_end=incident.window_end,
        trace_id="trace-1",
    )
    store.insert_report(
        incident,
        telemetry,
        [
            AnomalyOutput(
                node="checkout",
                anomaly_score=0.95,
                dev_vector={"error_rate": 0.95},
                first_deviation=NOW + timedelta(minutes=2),
            )
        ],
        result,
    )

    assert baselines[("checkout", "error_rate")].n == 60
    sql = "\n".join(statement for statement, _ in connection.executed).lower()
    assert "groundtruth" not in sql
    assert "from incidents" in sql
    assert "from anomaly_baseline" in sql
    assert "insert into rca_reports" in sql
    insert_params = connection.executed[-1][1]
    assert insert_params[3] == "checkout"
    assert json.loads(insert_params[7])[0]["node_id"] == "checkout"
    assert insert_params[13] == 0.8


def test_default_dsn_enforces_restricted_role(monkeypatch):
    monkeypatch.setenv("PGUSER", "causa")
    with pytest.raises(RuntimeError, match="rca_engine"):
        dsn_from_env()
