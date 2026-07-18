from datetime import datetime, timedelta, timezone

from contracts import (
    Baseline,
    CallType,
    Edge,
    IncidentTelemetry,
    MetricPointEnvelope,
)
from rca.orchestrator import RCAOrchestrator
from rca.store import IncidentState


NOW = datetime(2026, 7, 18, 4, 0, tzinfo=timezone.utc)


class _Reader:
    def __init__(self, telemetry):
        self.telemetry = telemetry
        self.calls = []
        self.closed = False

    def read_incident_window(self, **kwargs):
        self.calls.append(kwargs)
        return self.telemetry

    def close(self):
        self.closed = True


class _Store:
    def __init__(self, state, baselines):
        self.state = state
        self.baselines = baselines
        self.saved = []

    def load_incident(self, incident_id):
        assert incident_id == self.state.incident_id
        return self.state

    def load_baselines(self):
        return self.baselines

    def insert_report(self, state, telemetry, anomalies, result):
        self.saved.append((state, telemetry, anomalies, result))


def test_orchestrator_loads_exact_window_scores_and_persists():
    state = IncidentState(
        incident_id="inc-abc",
        alert_name="ServiceHighErrorRate",
        alerted_service="checkout",
        namespace="otel-demo",
        node=None,
        severity="critical",
        window_start=NOW,
        window_end=NOW + timedelta(minutes=11),
        trace_id="trace-1",
        telemetry_refs={},
        status="open",
        triggered_at=NOW + timedelta(minutes=10),
        resolved_at=None,
    )
    timestamps = [(NOW + timedelta(minutes=i)).timestamp() for i in range(3)]
    telemetry = IncidentTelemetry(
        incident_id=state.incident_id,
        alerted_service="checkout",
        window_start=state.window_start,
        window_end=state.window_end,
        trace_id=state.trace_id,
        edges=[Edge("checkout", "database", CallType.SYNC)],
        metric_points=[
            MetricPointEnvelope(
                service="database",
                metric="error_rate",
                value=10.0,
                series=[[timestamp, 10.0] for timestamp in timestamps],
            ),
            MetricPointEnvelope(
                service="checkout",
                metric="error_rate",
                value=8.0,
                series=[[timestamp, 8.0] for timestamp in timestamps],
            ),
        ],
    )
    store = _Store(
        state,
        {
            ("database", "error_rate"): Baseline(0.0, 1.0, 30),
            ("checkout", "error_rate"): Baseline(0.0, 1.0, 30),
        },
    )
    reader = _Reader(telemetry)

    result = RCAOrchestrator(store, reader_factory=lambda: reader).process("inc-abc")

    assert result.incident_id == "inc-abc"
    assert result.candidates
    assert store.saved[0][3] is result
    assert {output.node for output in store.saved[0][2]} == {"checkout", "database"}
    assert reader.calls[0]["around"] == NOW + timedelta(minutes=10)
    assert reader.calls[0]["lookback_minutes"] == 10
    assert reader.calls[0]["preferred_trace_id"] == "trace-1"
    assert reader.closed
