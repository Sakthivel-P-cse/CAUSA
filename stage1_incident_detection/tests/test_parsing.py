"""
Tempo span parsing — the call_type-enabling contract.

Asserts _parse_spans captures span kind + attributes and unwraps OTLP value
types, and that error messages fall back to span events. Without these fields,
Dev B's Influence Graph cannot be oriented (RCA_Engine_Spec §1.2).
"""

from __future__ import annotations

from ingest.clients.tempo_client import TempoClient
from contracts import SpanKind


def test_parse_spans_captures_kind_and_attributes(tempo_trace_payload):
    spans = TempoClient._parse_spans(tempo_trace_payload)
    by_id = {s.span_id: s for s in spans}

    # gRPC client span
    grpc = by_id["b2"]
    assert grpc.kind is SpanKind.CLIENT
    assert grpc.attributes.get("rpc.system") == "grpc"
    assert grpc.attributes.get("net.peer.name") == "productcatalogservice"

    # Kafka producer span
    kafka = by_id["b3"]
    assert kafka.kind is SpanKind.PRODUCER
    assert kafka.attributes.get("messaging.system") == "kafka"


def test_parse_spans_are_timezone_aware(tempo_trace_payload):
    spans = TempoClient._parse_spans(tempo_trace_payload)
    assert spans
    assert all(s.start_time.tzinfo is not None for s in spans)


def test_parse_spans_error_message_from_event(tempo_trace_payload):
    spans = TempoClient._parse_spans(tempo_trace_payload)
    err = next(s for s in spans if s.span_id == "c1")
    assert err.status_code == 2
    assert "productCatalogFailure" in (err.error_message or "")


def test_parse_spans_service_from_resource(tempo_trace_payload):
    spans = TempoClient._parse_spans(tempo_trace_payload)
    services = {s.service for s in spans}
    assert {"frontend", "checkoutservice", "productcatalogservice"} <= services


def test_attr_value_unwraps_otlp_types():
    from ingest.clients.tempo_client import _attr_value
    assert _attr_value({"stringValue": "x"}) == "x"
    assert _attr_value({"intValue": "5"}) == "5"
    assert _attr_value({"boolValue": True}) == "True"
    assert _attr_value({"arrayValue": {"values": [{"stringValue": "a"}, {"stringValue": "b"}]}}) == "a,b"
