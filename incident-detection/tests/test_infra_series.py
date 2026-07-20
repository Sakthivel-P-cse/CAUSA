"""
Node-level (infra) metric series — the input layer arbitration depends on.

get_infra_nodes() returns INSTANT cpu/mem, which gave infra nodes no series and
so no anomaly_score / FirstDeviationTime. arbitrate() requires both, so on live
telemetry infra could never be named a root cause; the S1/S5 fixtures only
passed because they inject infra anomaly by hand. These pin the range path and
the node-name identity that makes anomaly IDs match graph IDs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import respx

from ingest.clients.prometheus_client import PrometheusClient

START = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=10)


def _series(values):
    return httpx.Response(
        200, json={"status": "success", "data": {"result": [{"values": values}]}}
    )


@respx.mock
def test_node_cpu_series_prefers_relabeled_node_label():
    captured: list[str] = []

    def responder(request):
        q = request.url.params.get("query", "")
        captured.append(q)
        return _series([[1000, "91.5"], [1060, "93.0"]])

    respx.get("http://prom.local/api/v1/query_range").mock(side_effect=responder)
    c = PrometheusClient("http://prom.local")
    try:
        series = c.get_node_cpu_pct_series("kind-worker", START, END, "1m")
    finally:
        c.close()

    assert series == [[1000.0, 91.5], [1060.0, 93.0]]
    assert len(captured) == 1, "a `node`-labeled hit must not fall back"
    assert 'node="kind-worker"' in captured[0]
    assert "node_cpu_seconds_total" in captured[0]


@respx.mock
def test_node_series_falls_back_to_instance_ip_when_unrelabeled():
    """node-exporter without a `node` label must still resolve via kube_node_info."""
    range_queries: list[str] = []

    def instant(request):
        q = request.url.params.get("query", "")
        if "kube_node_info" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [
                {"metric": {"node": "kind-worker", "internal_ip": "10.0.0.5"},
                 "value": [0, "1"]},
            ]}})
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    def rng(request):
        q = request.url.params.get("query", "")
        range_queries.append(q)
        if 'node="kind-worker"' in q:
            return httpx.Response(
                200, json={"status": "success", "data": {"result": []}}
            )
        return _series([[1000, "77.0"]])

    respx.get("http://prom.local/api/v1/query").mock(side_effect=instant)
    respx.get("http://prom.local/api/v1/query_range").mock(side_effect=rng)
    c = PrometheusClient("http://prom.local")
    try:
        series = c.get_node_memory_pct_series("kind-worker", START, END, "1m")
    finally:
        c.close()

    assert series == [[1000.0, 77.0]]
    assert len(range_queries) == 2, "empty node-label result must trigger fallback"
    # Double-escaped on purpose (_esc_re): PromQL unescapes the quoted string to
    # `10\.0\.0\.5(:[0-9]+)?`, so the dots stay literal instead of matching any char.
    assert r'instance=~"10\\.0\\.0\\.5(:[0-9]+)?"' in range_queries[1]


@respx.mock
def test_node_series_absolute_window_and_step():
    captured: list[dict] = []

    def responder(request):
        captured.append(dict(request.url.params))
        return _series([[1000, "1"]])

    respx.get("http://prom.local/api/v1/query_range").mock(side_effect=responder)
    c = PrometheusClient("http://prom.local")
    try:
        c.get_node_cpu_pct_series("kind-worker", START, END, "30s")
        c.get_node_memory_pct_series("kind-worker", START, END, "30s")
    finally:
        c.close()

    assert captured, "expected range queries"
    for params in captured:
        assert float(params["start"]) == START.timestamp()
        assert float(params["end"]) == END.timestamp()   # never re-anchored at now
        assert params["step"] == "30s"


@respx.mock
def test_missing_node_ip_returns_empty_not_synthesized():
    """No kube_node_info row → [] rather than a faked series that poisons baselines."""
    respx.get("http://prom.local/api/v1/query").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"result": []}}
        )
    )
    respx.get("http://prom.local/api/v1/query_range").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"result": []}}
        )
    )
    c = PrometheusClient("http://prom.local")
    try:
        assert c.get_node_cpu_pct_series("ghost-node", START, END, "1m") == []
    finally:
        c.close()
