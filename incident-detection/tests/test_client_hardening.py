"""
Client hardening — audit fixes M7 (Tempo TraceQL error search), M8 (Loki level
line-filter fallback), M11 (PromQL label-value escaping). All over mocked HTTP.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import respx

from ingest.adapters import TelemetryAdapter
from ingest.clients.loki_client import LokiClient
from ingest.clients.prometheus_client import PrometheusClient
from ingest.clients.tempo_client import TempoClient
from contracts import Window


# ── M7: error-trace search must use TraceQL, not a status= tag ──────────

@respx.mock
def test_tempo_error_search_uses_traceql_status():
    captured: list[dict] = []

    def responder(request):
        captured.append(dict(request.url.params))
        return httpx.Response(200, json={"traces": [{"traceID": "abc"}]})

    respx.get("http://tempo.local/api/search").mock(side_effect=responder)
    c = TempoClient("http://tempo.local")
    try:
        ids = c.search_error_traces("checkout")
    finally:
        c.close()

    assert ids == ["abc"]
    params = captured[-1]
    assert "q" in params                                   # TraceQL path, not tags
    assert "status = error" in params["q"]
    assert 'resource.service.name = "checkout"' in params["q"]
    assert "status=error" not in params.get("tags", "")    # the old broken form is gone


# ── M11: service/namespace escaped before interpolation into PromQL ─────

@respx.mock
def test_prometheus_escapes_quotes_in_label_value():
    captured: list[dict] = []

    def responder(request):
        captured.append(dict(request.url.params))
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    respx.get("http://prom.local/api/v1/query_range").mock(side_effect=responder)
    c = PrometheusClient("http://prom.local")
    start = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)
    try:
        # a hostile service name that would break out of the selector unescaped
        c.get_error_rate_series('cart"} or up{', start, end, "30s")
    finally:
        c.close()

    q = captured[-1]["query"]
    assert 'server="cart\\"} or up{"' in q                 # the `"` is escaped
    assert 'server="cart"} or up{"' not in q               # never the raw, broken form


def test_prometheus_regex_escaper_neutralizes_metachars():
    # `.` is regex-escaped THEN string-escaped: the PromQL literal "a\\.b"
    # unescapes back to the regex a\.b, which matches a literal dot, not a
    # wildcard. So _esc_re("a.b") is the 5-char string a \ \ . b.
    assert PrometheusClient._esc_re("a.b") == "a\\\\.b"
    assert PrometheusClient._esc('x"y') == 'x\\"y'


# ── M8: logs(level=…) falls back to a line filter when level isn't a label ──

@respx.mock
def test_loki_level_falls_back_to_line_filter():
    def responder(request):
        q = request.url.params.get("query", "")
        # Simulate a stack with NO `level` stream label: only the line-filter
        # candidate (|~) matches; every label-based selector returns empty.
        if "|~" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [
                {"stream": {"service": "cart"},
                 "values": [["1752667200000000000", "boom: connection refused ERROR"]]},
            ]}})
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    respx.get("http://loki.local/loki/api/v1/query_range").mock(side_effect=responder)
    c = LokiClient("http://loki.local")
    start = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)
    try:
        entries = c.get_logs(service="cart", level="error", start=start, end=end)
    finally:
        c.close()

    assert entries, "line-filter fallback should have found the error log"
    assert entries[0]["level"] == "error"
    assert "connection refused" in entries[0]["message"]


# ── M9: a missing namespace for cpu/saturation is warned, not silent ────

def test_metrics_warns_once_on_missing_namespace(caplog):
    adapter = TelemetryAdapter(prom=object(), tempo=object(), loki=None, namespace=None)
    win = Window(
        datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 16, 12, 10, tzinfo=timezone.utc),
    )
    with caplog.at_level("WARNING"):
        assert adapter.metrics("cart", "cpu", win) == []
        assert adapter.metrics("cart", "cpu", win) == []          # 2nd call: no new warn
        assert adapter.metrics("cart", "saturation", win) == []

    warned = [r for r in caplog.records if "needs a namespace" in r.getMessage()]
    assert len(warned) == 2                                        # once per metric, not per call
