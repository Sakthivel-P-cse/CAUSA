"""
prometheus_client.py — Query Prometheus for metrics.

Talks to: prometheus.prometheus.svc.cluster.local:9090
Feeds:    ServiceEdge (service-dependency graph), InfraNode (infra graph),
          and per-service metric series (MetricPointEnvelope).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..contract import ServiceEdge, InfraNode
from ..service_scope import infer_service_name_from_pod

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PrometheusClient:
    """Thin wrapper around the Prometheus HTTP query API."""

    def __init__(self, base_url: str = "http://prometheus.prometheus.svc.cluster.local:9090"):
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(
            timeout=15.0,
            transport=httpx.HTTPTransport(retries=3),
        )

    def close(self) -> None:
        self._http.close()

    # ── Low-level helpers ─────────────────────────────────────────────

    def _instant(self, query: str, at: datetime | None = None) -> list[dict[str, Any]]:
        """Execute an instant PromQL query, return the result vector."""
        params: dict[str, Any] = {"query": query}
        if at:
            params["time"] = at.timestamp()
        resp = self._http.get(f"{self._base}/api/v1/query", params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {data.get('error', 'unknown')}")
        return data["data"]["result"]

    def _range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "30s",
    ) -> list[dict[str, Any]]:
        """Execute a range PromQL query."""
        params = {
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step,
        }
        resp = self._http.get(f"{self._base}/api/v1/query_range", params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Prometheus range query failed: {data.get('error', 'unknown')}")
        return data["data"]["result"]

    @staticmethod
    def _scalar(result: list[dict], default: float = 0.0) -> float:
        """Pull the float value from a single-element instant result."""
        if not result:
            return default
        return float(result[0]["value"][1])

    # ── Service Dependency edges ──────────────────────────────────────

    def get_service_edges(self, window: str = "5m") -> list[ServiceEdge]:
        """
        Build directed service call edges from Tempo service-graph metrics.

        Returns edges for every (caller, callee) pair that has had at least
        one request in the last `window`.
        """
        req_query = f"sum by (client, server) (rate(traces_service_graph_request_total[{window}]))"
        err_query = (
            f"sum by (client, server) "
            f"(rate(traces_service_graph_request_failed_total[{window}]))"
        )
        lat_query = (
            f"histogram_quantile(0.99, sum by (client, server, le) "
            f"(rate(traces_service_graph_request_server_seconds_bucket[{window}]))) * 1000"
        )

        req_results = self._instant(req_query)
        err_results = self._instant(err_query)
        lat_results = self._instant(lat_query)

        # Index error rates and latencies by (client, server)
        err_map: dict[tuple[str, str], float] = {}
        for r in err_results:
            key = (r["metric"].get("client", ""), r["metric"].get("server", ""))
            err_map[key] = float(r["value"][1])

        lat_map: dict[tuple[str, str], float] = {}
        for r in lat_results:
            key = (r["metric"].get("client", ""), r["metric"].get("server", ""))
            lat_map[key] = float(r["value"][1])

        edges: list[ServiceEdge] = []
        for r in req_results:
            caller = r["metric"].get("client", "unknown")
            callee = r["metric"].get("server", "unknown")
            if not caller or not callee:
                continue
            req_rate = float(r["value"][1])
            err_rate_abs = err_map.get((caller, callee), 0.0)
            err_rate_pct = (err_rate_abs / req_rate) if req_rate > 0 else 0.0
            latency = lat_map.get((caller, callee), 0.0)
            edges.append(ServiceEdge(
                caller=caller,
                callee=callee,
                request_rate=req_rate,
                error_rate=err_rate_pct,
                latency_p99_ms=latency,
            ))

        logger.info("Fetched %d service edges", len(edges))
        return edges

    # ── Infrastructure nodes ──────────────────────────────────────────

    def get_infra_nodes(self) -> list[InfraNode]:
        """
        Collect pod restart counts, node conditions, and node resource
        usage to populate the infrastructure layer.
        """
        nodes: list[InfraNode] = []

        # Pod -> node assignment for topology views
        pod_info = self._instant("kube_pod_info")
        pod_host_map: dict[tuple[str, str], str] = {}
        for r in pod_info:
            ns = r["metric"].get("namespace", "")
            pod = r["metric"].get("pod", "")
            host_node = r["metric"].get("node", "")
            if ns and pod and host_node:
                pod_host_map[(ns, pod)] = host_node

        # Pod restart counts
        restarts = self._instant(
            "sum by (namespace, pod) (kube_pod_container_status_restarts_total)"
        )
        restart_map: dict[tuple[str, str], int] = {}
        for r in restarts:
            ns = r["metric"].get("namespace", "")
            pod = r["metric"].get("pod", "")
            restart_map[(ns, pod)] = int(float(r["value"][1]))

        # Pod phase
        pod_phase = self._instant("kube_pod_status_phase{phase='Running'} == 1")
        for r in pod_phase:
            ns = r["metric"].get("namespace", "")
            pod = r["metric"].get("pod", "")
            nodes.append(InfraNode(
                name=pod,
                kind="pod",
                namespace=ns,
                cpu_usage_pct=None,
                memory_usage_pct=None,
                restart_count=restart_map.get((ns, pod), 0),
                condition="Running",
                host_node=pod_host_map.get((ns, pod)),
                service_name=infer_service_name_from_pod(pod),
            ))

        # Node memory pressure
        node_conds = self._instant(
            "kube_node_status_condition{condition='MemoryPressure',status='true'} == 1"
        )
        for r in node_conds:
            node_name = r["metric"].get("node", "unknown")
            nodes.append(InfraNode(
                name=node_name,
                kind="node",
                namespace=None,
                cpu_usage_pct=None,
                memory_usage_pct=None,
                restart_count=None,
                condition="MemoryPressure",
                host_node=None,
                service_name=None,
            ))

        # Node CPU saturation (usage %)
        cpu_results = self._instant(
            "100 - (avg by (instance) (rate(node_cpu_seconds_total{mode='idle'}[2m])) * 100)"
        )
        for r in cpu_results:
            instance = r["metric"].get("instance", "unknown")
            nodes.append(InfraNode(
                name=instance,
                kind="node",
                namespace=None,
                cpu_usage_pct=float(r["value"][1]),
                memory_usage_pct=None,
                restart_count=None,
                condition=None,
                host_node=None,
                service_name=None,
            ))

        # Node memory usage % — the value that drives the NodeMemoryPressure
        # alert (fires at < 10% available).
        mem_results = self._instant(
            "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100"
        )
        for r in mem_results:
            instance = r["metric"].get("instance", "unknown")
            nodes.append(InfraNode(
                name=instance,
                kind="node",
                namespace=None,
                cpu_usage_pct=None,
                memory_usage_pct=float(r["value"][1]),
                restart_count=None,
                condition=None,
                host_node=None,
                service_name=None,
            ))

        logger.info("Fetched %d infra nodes", len(nodes))
        return nodes

    # ── Metric series (for MetricPointEnvelope) ───────────────────────

    def get_error_rate_series(
        self,
        service: str,
        window_minutes: int = 30,
    ) -> list[list[float]]:
        """Return the error rate time series for a service over the last N minutes."""
        end = _utcnow()
        start = end - timedelta(minutes=window_minutes)
        results = self._range(
            query=(
                f'sum(rate(traces_service_graph_request_failed_total'
                f'{{server="{service}"}}[1m])) / '
                f'sum(rate(traces_service_graph_request_total'
                f'{{server="{service}"}}[1m]))'
            ),
            start=start,
            end=end,
            step="1m",
        )
        if not results:
            return []
        return [[float(v[0]), float(v[1])] for v in results[0]["values"] if v[1] != "NaN"]

    def get_latency_p99_series(
        self,
        service: str,
        window_minutes: int = 30,
    ) -> list[list[float]]:
        """Return the p99 latency (ms) time series for a service."""
        end = _utcnow()
        start = end - timedelta(minutes=window_minutes)
        results = self._range(
            query=(
                f'histogram_quantile(0.99, sum by (le) '
                f'(rate(traces_service_graph_request_server_seconds_bucket'
                f'{{server="{service}"}}[1m]))) * 1000'
            ),
            start=start,
            end=end,
            step="1m",
        )
        if not results:
            return []
        return [[float(v[0]), float(v[1])] for v in results[0]["values"] if v[1] != "NaN"]

    def get_request_rate_series(
        self,
        service: str,
        window_minutes: int = 30,
    ) -> list[list[float]]:
        """Return the request rate time series for a service."""
        end = _utcnow()
        start = end - timedelta(minutes=window_minutes)
        results = self._range(
            query=(
                f'sum(rate(traces_service_graph_request_total'
                f'{{server="{service}"}}[1m]))'
            ),
            start=start,
            end=end,
            step="1m",
        )
        if not results:
            return []
        return [[float(v[0]), float(v[1])] for v in results[0]["values"] if v[1] != "NaN"]

    def get_container_memory_pct_series(
        self,
        service: str,
        namespace: str,
        window_minutes: int = 30,
    ) -> list[float]:
        """Return container memory usage % (vs limit) for a service."""
        end = _utcnow()
        start = end - timedelta(minutes=window_minutes)

        pod_selector = (
            f'(kube_pod_labels{{namespace="{namespace}",label_app="{service}"}} or '
            f'kube_pod_labels{{namespace="{namespace}",label_app_kubernetes_io_name="{service}"}} or '
            f'kube_pod_labels{{namespace="{namespace}",label_k8s_app="{service}"}})'
        )

        query_label = (
            f'sum(container_memory_usage_bytes{{namespace="{namespace}",container!=""}} '
            f'* on(pod) group_left() {pod_selector}) '
            f'/ sum(kube_pod_container_resource_limits{{namespace="{namespace}",resource="memory"}} '
            f'* on(pod) group_left() {pod_selector})'
        )

        results = self._range(query_label, start=start, end=end, step="1m")

        if not results:
            query_prefix = (
                f'sum(container_memory_usage_bytes{{namespace="{namespace}",pod=~"{service}-.*"}}) '
                f'/ sum(kube_pod_container_resource_limits'
                f'{{namespace="{namespace}",pod=~"{service}-.*",resource="memory"}})'
            )
            results = self._range(query_prefix, start=start, end=end, step="1m")

        if not results:
            return []
        return [float(v[1]) for v in results[0]["values"] if v[1] != "NaN"]

    def get_container_cpu_pct_series(
        self,
        service: str,
        namespace: str,
        window_minutes: int = 30,
    ) -> list[float]:
        """Return container CPU usage % (vs limit) for a service."""
        end = _utcnow()
        start = end - timedelta(minutes=window_minutes)

        pod_selector = (
            f'(kube_pod_labels{{namespace="{namespace}",label_app="{service}"}} or '
            f'kube_pod_labels{{namespace="{namespace}",label_app_kubernetes_io_name="{service}"}} or '
            f'kube_pod_labels{{namespace="{namespace}",label_k8s_app="{service}"}})'
        )

        query_label = (
            f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",container!=""}}[2m]) '
            f'* on(pod) group_left() {pod_selector}) '
            f'/ sum(kube_pod_container_resource_limits{{namespace="{namespace}",resource="cpu"}} '
            f'* on(pod) group_left() {pod_selector}) * 100'
        )

        results = self._range(query_label, start=start, end=end, step="1m")

        if not results:
            query_prefix = (
                f'sum(rate(container_cpu_usage_seconds_total'
                f'{{namespace="{namespace}",pod=~"{service}-.*",container!=""}}[2m])) '
                f'/ sum(kube_pod_container_resource_limits'
                f'{{namespace="{namespace}",pod=~"{service}-.*",resource="cpu"}}) * 100'
            )
            results = self._range(query_prefix, start=start, end=end, step="1m")

        if not results:
            return []
        return [float(v[1]) for v in results[0]["values"] if v[1] != "NaN"]
