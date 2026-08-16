"""
Prometheus client: window threading (#2) + infra-node key merge (#7).

Series MUST query the ABSOLUTE [start, end] with the caller's step (never
re-anchor at now), and node-exporter series keyed on `instance` must merge with
the pod condition rows keyed on the k8s `node` name.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import respx

from ingest.clients.prometheus_client import PrometheusClient


def _range_ok():
    return httpx.Response(200, json={"status": "success",
                                     "data": {"result": [{"values": [[1000, "1"]]}]}})


@respx.mock
def test_series_query_absolute_window_and_step():
    captured: list[dict] = []

    def responder(request):
        captured.append(dict(request.url.params))
        return _range_ok()

    respx.get("http://prom.local/api/v1/query_range").mock(side_effect=responder)
    c = PrometheusClient("http://prom.local")
    start = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=10)
    try:
        c.get_error_rate_series("cart", start, end, "30s")
        c.get_latency_p95_series("cart", start, end, "30s")
    finally:
        c.close()

    for params in captured:
        assert float(params["start"]) == start.timestamp()
        assert float(params["end"]) == end.timestamp()          # not "now"
        assert params["step"] == "30s"                          # shared step honored


@respx.mock
def test_infra_nodes_merge_instance_and_node_keys():
    def responder(request):
        q = request.url.params.get("query", "")
        if "kube_node_info" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [
                {"metric": {"node": "kind-worker", "internal_ip": "10.0.0.5"}, "value": [0, "1"]},
            ]}})
        if "MemoryPressure" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [
                {"metric": {"node": "kind-worker"}, "value": [0, "1"]},
            ]}})
        if "node_cpu_seconds_total" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [
                {"metric": {"instance": "10.0.0.5:9100"}, "value": [0, "88.0"]},
            ]}})
        if "node_memory_MemAvailable_bytes" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [
                {"metric": {"instance": "10.0.0.5:9100"}, "value": [0, "72.0"]},
            ]}})
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    respx.get("http://prom.local/api/v1/query").mock(side_effect=responder)
    c = PrometheusClient("http://prom.local")
    try:
        nodes = c.get_infra_nodes()
    finally:
        c.close()

    worker = [n for n in nodes if n.name == "kind-worker"]
    assert len(worker) == 1, "instance- and node-keyed rows must merge into ONE node"
    n = worker[0]
    assert n.condition == "MemoryPressure"
    assert n.cpu_usage_pct == 88.0
    assert n.memory_usage_pct == 72.0
