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

    def insert_report(self, state, telemetry, anomalies, result, thresholds=None):
        self.saved.append((state, telemetry, anomalies, result, thresholds))


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
    assert reader.calls[0]["window_start"] == state.window_start
    assert reader.calls[0]["window_end"] == state.window_end
    assert reader.calls[0]["preferred_trace_id"] == "trace-1"
    assert reader.closed
    assert result.fault_class is not None or result.candidates


def test_orchestrator_empty_baselines_loud_abstain():
    state = IncidentState(
        incident_id="inc-empty-bl",
        alert_name="ServiceHighErrorRate",
        alerted_service="checkout",
        namespace="otel-demo",
        node=None,
        severity="critical",
        window_start=NOW,
        window_end=NOW + timedelta(minutes=11),
        trace_id=None,
        telemetry_refs={},
        status="open",
        triggered_at=NOW + timedelta(minutes=10),
        resolved_at=None,
    )
    store = _Store(state, {})
    reader = _Reader(
        IncidentTelemetry(
            incident_id=state.incident_id,
            alerted_service="checkout",
            window_start=state.window_start,
            window_end=state.window_end,
        )
    )
    from rca.orchestrator import MISSING_BASELINES_REASON

    result = RCAOrchestrator(store, reader_factory=lambda: reader).process(
        "inc-empty-bl"
    )
    assert result.abstain_reason == MISSING_BASELINES_REASON
    assert result.candidates == []
    assert store.saved
    assert not reader.calls


def test_load_telemetry_preserves_short_and_fractional_windows_and_closes():
    from types import SimpleNamespace

    for duration in (timedelta(seconds=30), timedelta(seconds=90), timedelta(minutes=11)):
        state = SimpleNamespace(incident_id='i', alerted_service='svc', namespace=None,
                                window_start=NOW, window_end=NOW + duration, trace_id=None)
        reader = _Reader(None)
        RCAOrchestrator(_Store(None, {}), reader_factory=lambda: reader)._load_telemetry(state)
        assert reader.calls[0]['window_start'] == NOW
        assert reader.calls[0]['window_end'] == NOW + duration
        assert reader.closed


def test_load_telemetry_rejects_invalid_window_and_closes_on_reader_failure():
    from types import SimpleNamespace
    import pytest

    state = SimpleNamespace(incident_id='i', alerted_service='svc', namespace=None,
                            window_start=NOW, window_end=NOW, trace_id=None)
    reader = _Reader(None)
    orchestrator = RCAOrchestrator(_Store(None, {}), reader_factory=lambda: reader)
    for start, end in ((None, NOW), (NOW, NOW), (NOW, NOW - timedelta(seconds=1))):
        state.window_start, state.window_end = start, end
        with pytest.raises(ValueError):
            orchestrator._load_telemetry(state)
    assert not reader.calls
    state.window_start, state.window_end = NOW, NOW + timedelta(seconds=30)
    def fail(**kwargs):
        raise RuntimeError('read failed')
    reader.read_incident_window = fail
    with pytest.raises(RuntimeError, match='read failed'):
        orchestrator._load_telemetry(state)
    assert reader.closed
