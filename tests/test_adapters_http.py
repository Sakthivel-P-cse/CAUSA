"""
End-to-end client wiring over mocked HTTP (respx).

Proves get_trace hits the right Tempo endpoint and parses the response into
kind/attribute-bearing TraceSpans — the full path a live adapter takes.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ingest.clients.tempo_client import TempoClient
from contracts import SpanKind


@respx.mock
def test_get_trace_end_to_end(tempo_trace_payload):
    trace_id = "0af7651916cd43dd8448eb211c80319c"
    route = respx.get(f"http://tempo.local/api/traces/{trace_id}").mock(
        return_value=httpx.Response(200, json=tempo_trace_payload)
    )
    client = TempoClient("http://tempo.local")
    try:
        spans = client.get_trace(trace_id)
    finally:
        client.close()

    assert route.called
    kafka = next(s for s in spans if s.span_id == "b3")
    assert kafka.kind is SpanKind.PRODUCER
    assert kafka.attributes.get("messaging.system") == "kafka"


@respx.mock
def test_search_swallows_errors_to_empty():
    respx.get("http://tempo.local/api/search").mock(
        return_value=httpx.Response(500, text="boom")
    )
    client = TempoClient("http://tempo.local")
    try:
        assert client.search_traces("frontend") == []
    finally:
        client.close()
