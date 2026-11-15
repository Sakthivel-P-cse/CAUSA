"""Contract tests for the canonical Dev A-to-B adapter."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from contracts import (
    AnomalyOutput,
    CallType,
    IncidentTelemetry,
    NodeKind,
    RankedResult,
    Role,
    RootCauseCandidate,
    SignalScores,
    SpanEnvelope,
    SpanKind,
    Thresholds,
    TraceSpan,
)
from graphs.incident_subgraph import THETA_LOW, build_incident_subgraph
from rca.adapter import adapt_incident


def test_golden_otel_fixture_adapts_to_kernel(golden_dir):
    telemetry_data = json.loads((golden_dir / "telemetry.json").read_text())
    anomaly_data = json.loads((golden_dir / "anomaly_output.json").read_text())
    telemetry = IncidentTelemetry.from_fixture(telemetry_data)
    anomalies = [
        AnomalyOutput(
            node=node,
            anomaly_score=value["anomaly_score"],
            dev_vector=value["dev_vector"],
            first_deviation=(
                datetime.fromisoformat(value["first_deviation"])
                if value["first_deviation"]
                else None
            ),
        )
        for node, value in anomaly_data["nodes"].items()
    ]

    adapted = adapt_incident(telemetry, anomalies)

    assert adapted.anomaly.thresholds == Thresholds()
    assert adapted.anomaly.first_deviation_time["product-catalog"] == 240.0
    assert adapted.anomaly.first_deviation_time["valkey-cart"] is None
    assert telemetry.trace_id in adapted.anomaly.anomalous_trace_ids
    assert any(
        edge.src == "frontend"
        and edge.dst == "product-catalog"
        and edge.call_type is CallType.SYNC
        and edge.degraded
        for edge in adapted.topology.edges
    )
    assert next(
        node for node in adapted.topology.nodes if node.node_id == "node-worker-1"
    ).kind is NodeKind.INFRA


def test_adapter_uses_canonical_theta_low(golden_dir):
    telemetry_data = json.loads((golden_dir / "telemetry.json").read_text())
    anomaly_data = json.loads((golden_dir / "anomaly_output.json").read_text())
    telemetry = IncidentTelemetry.from_fixture(telemetry_data)
    anomalies = [
        AnomalyOutput(
            node=node,
            anomaly_score=value["anomaly_score"],
            dev_vector=value["dev_vector"],
            first_deviation=(
                datetime.fromisoformat(value["first_deviation"])
                if value["first_deviation"]
                else None
            ),
        )
        for node, value in anomaly_data["nodes"].items()
    ]
    adapted = adapt_incident(telemetry, anomalies)
    subgraph = build_incident_subgraph(
        adapted.topology,
        adapted.traces,
        adapted.anomaly,
    )

    assert THETA_LOW == Thresholds().theta_low == 0.25
    assert subgraph.theta_low == 0.25


def test_c3_ranked_result_is_json_serializable():
    result = RankedResult(
        incident_id="incident-1",
        candidates=[
            RootCauseCandidate(
                node_id="service-a",
                node_kind=NodeKind.SERVICE,
                score=0.9,
                confidence=0.8,
                role=Role.ORIGIN,
                signals=SignalScores(T=1.0, D=0.9, P=0.8),
                dev_vector={"error_rate": 0.9},
                explanation="service-a is the origin.",
            )
        ],
    )

    encoded = json.dumps(result.to_dict())
    assert '"node_kind": "service"' in encoded
    assert '"role": "origin"' in encoded


def test_adapter_derives_trace_and_resource_annotations():
    start = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)
    telemetry = IncidentTelemetry(
        incident_id="incident-2",
        alerted_service="api",
        window_start=start,
        window_end=start + timedelta(minutes=10),
        spans=[
            SpanEnvelope(
                span=TraceSpan(
                    span_id="span-1",
                    parent_span_id=None,
                    service="api",
                    operation="GET /",
                    duration_ms=25.0,
                    status_code=2,
                    error_message="failure",
                    start_time=start + timedelta(seconds=3),
                    kind=SpanKind.SERVER,
                ),
                trace_id="trace-1",
            )
        ],
    )
    anomalies = [
        AnomalyOutput(
            node="api",
            anomaly_score=0.8,
            dev_vector={"cpu": 0.7},
            first_deviation=start + timedelta(seconds=2),
        )
    ]

    adapted = adapt_incident(telemetry, anomalies)

    assert adapted.traces[0].spans[0].start_ms == 3000.0
    assert adapted.traces[0].spans[0].end_ms == 3025.0
    assert adapted.traces[0].anomalous
    assert adapted.anomaly.anomalous_trace_ids == frozenset({"trace-1"})
    assert adapted.anomaly.resource_deviation["api"]
