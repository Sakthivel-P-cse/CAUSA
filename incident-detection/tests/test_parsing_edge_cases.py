"""
Parser hardening — Tempo AnyValue / timestamps / span kind, and Loki escaping.
"""

from __future__ import annotations

from ingest.clients.tempo_client import _attr_value, TempoClient
from ingest.clients.loki_client import LokiClient
from contracts import SpanKind


def test_attr_value_covers_the_anyvalue_union():
    assert _attr_value({"stringValue": "x"}) == "x"
    assert _attr_value({"intValue": 3}) == "3"
    assert _attr_value({"boolValue": True}) == "True"
    assert _attr_value({"bytesValue": "deadbeef"}) == "deadbeef"
    assert _attr_value({"kvlistValue": {"values": [
        {"key": "a", "value": {"stringValue": "1"}},
        {"key": "b", "value": {"intValue": 2}},
    ]}}) == "a=1,b=2"
    # a real JSON null becomes "" — never the literal string "None"
    assert _attr_value(None) == ""
    assert _attr_value({"stringValue": None}) == ""


def test_parse_drops_span_with_missing_start_no_1970():
    raw = {"batches": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "cart"}}]},
        "scopeSpans": [{"spans": [
            {"spanId": "ok", "startTimeUnixNano": "1700000000000000000",
             "endTimeUnixNano": "1700000000500000000", "name": "good"},
            {"spanId": "bad", "name": "missing-start"},          # no start → dropped
        ]}],
    }]}
    spans = TempoClient._parse_spans(raw)
    ids = {s.span_id for s in spans}
    assert "ok" in ids and "bad" not in ids
    good = next(s for s in spans if s.span_id == "ok")
    assert good.duration_ms == 500.0
    assert good.start_time.year == 2023        # not 1970


def test_from_otlp_numeric_string_kind():
    assert SpanKind.from_otlp("3") is SpanKind.CLIENT      # not silently INTERNAL
    assert SpanKind.from_otlp(3) is SpanKind.CLIENT
    assert SpanKind.from_otlp("SPAN_KIND_SERVER") is SpanKind.SERVER
    assert SpanKind.from_otlp("garbage") is SpanKind.INTERNAL


def test_loki_escapes_service_and_level_in_matchers():
    c = LokiClient("http://loki.local")
    try:
        sels = c._candidate_selectors(service='a"b', level='c"d')
    finally:
        c.close()
    blob = "\n".join(sels)
    # the raw quote must be backslash-escaped, never left to break the matcher
    assert 'a"b' not in blob
    assert '\\"' in blob
