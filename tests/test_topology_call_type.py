"""
Edge call_type derivation — Dev B's contract. Assert it hard.

kafka producer → async, gRPC/DB client → sync, kube_pod_info → runs-on Edge.
"""

from __future__ import annotations

from ingest.clients.tempo_client import TempoClient
from ingest.topology import (
    build_topology,
    call_types_from_spans,
    classify_call,
    stamp_service_edges,
)
from contracts import CallType, InfraNode, ServiceEdge, SpanKind, TraceSpan


def _span(span_id, service, kind, attrs=None, parent=None):
    from datetime import datetime, timezone
    return TraceSpan(
        span_id=span_id, parent_span_id=parent, service=service,
        operation="op", duration_ms=1.0, status_code=1, error_message=None,
        start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        kind=kind, attributes=attrs or {},
    )


def test_classify_grpc_is_sync():
    s = _span("1", "frontend", SpanKind.CLIENT, {"rpc.system": "grpc"})
    assert classify_call(s) is CallType.SYNC


def test_classify_db_is_sync():
    s = _span("1", "svc", SpanKind.CLIENT, {"db.system": "postgresql"})
    assert classify_call(s) is CallType.SYNC


def test_classify_kafka_is_async():
    s = _span("1", "checkout", SpanKind.PRODUCER, {"messaging.system": "kafka"})
    assert classify_call(s) is CallType.ASYNC


def test_call_types_from_real_trace(tempo_trace_payload):
    spans = TempoClient._parse_spans(tempo_trace_payload)
    ct = call_types_from_spans(spans)
    # checkoutservice --grpc--> productcatalogservice : sync
    assert ct[("checkoutservice", "productcatalogservice")] is CallType.SYNC


def test_async_dominates_and_runs_on_edge():
    spans = [
        _span("p", "checkout", SpanKind.PRODUCER, {"messaging.system": "kafka"}),
        _span("cc", "cartservice", SpanKind.CONSUMER, parent="p"),
    ]
    edges = [ServiceEdge("checkout", "cartservice", 10.0, 0.0, 5.0)]
    stamp_service_edges(edges, spans)
    assert edges[0].call_type is CallType.ASYNC

    infra = [
        InfraNode("pod-a", "pod", "otel-demo", None, None, 0, "Running",
                  host_node="node-1", service_name="cartservice"),
    ]
    topo = build_topology(edges, infra)
    triples = {(e.src, e.dst, e.call_type) for e in topo}
    assert ("checkout", "cartservice", CallType.ASYNC) in triples
    assert ("node-1", "cartservice", CallType.RUNS_ON) in triples


def test_unknown_pair_defaults_sync():
    edges = [ServiceEdge("a", "b", 1.0, 0.0, 1.0)]
    stamp_service_edges(edges, [])  # no spans → no evidence
    assert edges[0].call_type is CallType.SYNC
