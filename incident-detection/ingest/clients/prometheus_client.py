"""
prometheus_client.py — Query Prometheus for metrics.

Talks to: prometheus.prometheus.svc.cluster.local:9090
Feeds:    ServiceEdge (service-dependency graph), InfraNode (infra graph),
          and per-service metric series (MetricPointEnvelope).

Hardening vs the previous version:
  • each PromQL query is isolated in try/except — one failing query no longer
    aborts the whole fetch (partial telemetry beats none);
  • InfraNode rows are merged by (kind, namespace, name) instead of emitting
    4–5 partial rows per node;
  • container CPU/mem series return real [[ts, val]] pairs (no synthetic
    timestamps downstream — anomaly baselines need real time).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import httpx

from contracts import InfraNode, ServiceEdge
from ..service_scope import infer_service_name_from_pod

logger = logging.getLogger(__name__)


def _series_points(values: list[list[Any]]) -> list[list[float]]:
    """Prometheus range `values` → [[ts, val]], dropping NaN samples."""
    out: list[list[float]] = []
    for v in values:
        if v[1] == "NaN":
            continue
        try:
            out.append([float(v[0]), float(v[1])])
        except (TypeError, ValueError):
            continue
    return out


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

    def _instant_safe(self, query: str, at: datetime | None = None) -> list[dict[str, Any]]:
        """`_instant` but returns [] on any failure (one bad query != total loss)."""
        try:
            return self._instant(query, at=at)
        except Exception as exc:
            logger.warning("Prometheus instant query failed (%s): %s", query[:60], exc)
            return []

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

    def _range_safe(
        self, query: str, start: datetime, end: datetime, step: str = "1m"
    ) -> list[dict[str, Any]]:
        try:
            return self._range(query, start, end, step=step)
        except Exception as exc:
            logger.warning("Prometheus range query failed (%s): %s", query[:60], exc)
            return []

    @staticmethod
    def _scalar(result: list[dict], default: float = 0.0) -> float:
        """Pull the float value from a single-element instant result."""
        if not result:
            return default
        return float(result[0]["value"][1])

    # ── Label-value escaping (audit M11) ──────────────────────────────
    #
    # `service`/`namespace` come from telemetry (span service.name) and
    # Alertmanager labels, not trusted constants, so they must be escaped before
    # being interpolated into PromQL — otherwise a `"` breaks the selector or
    # injects extra matchers (the Loki client already does this; parity here).

    @staticmethod
    def _esc(value: str) -> str:
        """Escape a value for a double-quoted PromQL matcher (`=` / `!=`)."""
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _esc_re(value: str) -> str:
        """Escape a value used inside a PromQL regex matcher (`=~` / `!~`):
        regex-escape metacharacters (so a `.` in a name isn't a wildcard), then
        string-escape the result for the quoted literal."""
        return PrometheusClient._esc(re.escape(value))

    # ── Service Dependency edges ──────────────────────────────────────

    def get_service_edges(self, window: str = "5m") -> list[ServiceEdge]:
        """
        Build directed service call edges from Tempo service-graph metrics.

        Returns edges for every (caller, callee) pair that has had at least
        one request in the last `window`. `call_type` is left at its default
        (sync) here — ingest.topology stamps sync/async from span kind, since
        the service-graph metric carries no transport dimension.
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

        req_results = self._instant_safe(req_query)
        err_results = self._instant_safe(err_query)
        lat_results = self._instant_safe(lat_query)

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
        Collect pod restart counts, node conditions, and node resource usage.

        Rows are merged by (kind, namespace, name): a node with both CPU% and
        mem% appears ONCE, not as two partial rows. Each PromQL query is
        isolated so one failure yields partial (not empty) infra state.
        """
        merged: dict[tuple[str, str | None, str], InfraNode] = {}

        def upsert(key: tuple[str, str | None, str], **fields: Any) -> InfraNode:
            node = merged.get(key)
            if node is None:
                node = InfraNode(
                    name=key[2], kind=key[0], namespace=key[1],
                    cpu_usage_pct=None, memory_usage_pct=None,
                    restart_count=None, condition=None,
                )
                merged[key] = node
            for name, value in fields.items():
                if value is not None:
                    setattr(node, name, value)
            return node

        # Pod → node assignment (the runs-on relationship's source).
        pod_host_map: dict[tuple[str, str], str] = {}
        for r in self._instant_safe("kube_pod_info"):
            ns = r["metric"].get("namespace", "")
            pod = r["metric"].get("pod", "")
            host_node = r["metric"].get("node", "")
            if ns and pod and host_node:
                pod_host_map[(ns, pod)] = host_node

        # Pod restart counts
        restart_map: dict[tuple[str, str], int] = {}
        for r in self._instant_safe(
            "sum by (namespace, pod) (kube_pod_container_status_restarts_total)"
        ):
            ns = r["metric"].get("namespace", "")
            pod = r["metric"].get("pod", "")
            if pod:
                restart_map[(ns, pod)] = int(float(r["value"][1]))

        # Pods (phase Running)
        for r in self._instant_safe("kube_pod_status_phase{phase='Running'} == 1"):
            ns = r["metric"].get("namespace", "")
            pod = r["metric"].get("pod", "")
            if not pod:
                continue
            upsert(
                ("pod", ns, pod),
                restart_count=restart_map.get((ns, pod), 0),
                condition="Running",
                host_node=pod_host_map.get((ns, pod)),
                service_name=infer_service_name_from_pod(pod),
            )

        # instance → k8s node-name resolver. node-exporter series key on
        # `instance` (typically IP:port); pod conditions and runs-on edges key
        # on the k8s `node` name (kube_pod_info.node). Without this, the same
        # physical node produces two un-mergeable keys and the saturated node is
        # orphaned from its condition row → S5 infra attribution breaks (#7).
        # kube_node_info carries both `node` and `internal_ip`.
        ip_to_node: dict[str, str] = {}
        for r in self._instant_safe("kube_node_info"):
            node = r["metric"].get("node", "")
            ip = r["metric"].get("internal_ip", "")
            if node and ip:
                ip_to_node[ip] = node

        def node_key(metric: dict[str, Any]) -> str:
            # Prefer an explicit `node` label (relabeled node-exporter); else
            # resolve the instance's host:port via internal_ip; else fall back
            # to the raw instance so the row is never silently dropped.
            if metric.get("node"):
                return metric["node"]
            instance = metric.get("instance", "unknown")
            host = instance.rsplit(":", 1)[0]
            return ip_to_node.get(host, instance)

        # Node memory pressure condition
        for r in self._instant_safe(
            "kube_node_status_condition{condition='MemoryPressure',status='true'} == 1"
        ):
            upsert(("node", None, node_key(r["metric"])), condition="MemoryPressure")

        # Node CPU saturation (usage %)
        for r in self._instant_safe(
            "100 - (avg by (instance) (rate(node_cpu_seconds_total{mode='idle'}[2m])) * 100)"
        ):
            upsert(("node", None, node_key(r["metric"])), cpu_usage_pct=float(r["value"][1]))

        # Node memory usage %
        for r in self._instant_safe(
            "(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100"
        ):
            upsert(("node", None, node_key(r["metric"])), memory_usage_pct=float(r["value"][1]))

        nodes = list(merged.values())
        logger.info("Fetched %d infra nodes", len(nodes))
        return nodes

    # ── Metric series (for MetricPointEnvelope) ───────────────────────
    #
    # Every series method takes ABSOLUTE [start, end] bounds and a shared step,
    # threaded down from the caller's Window. They MUST NOT re-anchor `end` at
    # now(): warmup baselines read a historical healthy window, and a fault
    # replay reads a past incident window — anchoring at now() would read the
    # wrong data and silently poison the baseline (Dev_A "injecting a fault must
    # not shift the baseline" invariant). `end` defaulting to now is provided
    # only as an explicit convenience, never as a hidden default.

    def get_error_rate_series(
        self, service: str, start: datetime, end: datetime, step: str = "1m"
    ) -> list[list[float]]:
        """Error rate time series for a service over [start, end]."""
        svc = self._esc(service)
        results = self._range_safe(
            (
                f'sum(rate(traces_service_graph_request_failed_total'
                f'{{server="{svc}"}}[1m])) / '
                f'sum(rate(traces_service_graph_request_total'
                f'{{server="{svc}"}}[1m]))'
            ),
            start, end, step=step,
        )
        return _series_points(results[0]["values"]) if results else []

    def get_latency_p95_series(
        self, service: str, start: datetime, end: datetime, step: str = "1m"
    ) -> list[list[float]]:
        """p95 latency (ms) time series for a service. (Anomaly metric `p95_latency`.)"""
        return self._latency_series(service, 0.95, start, end, step)

    def get_latency_p99_series(
        self, service: str, start: datetime, end: datetime, step: str = "1m"
    ) -> list[list[float]]:
        """p99 latency (ms) time series for a service."""
        return self._latency_series(service, 0.99, start, end, step)

    def _latency_series(
        self, service: str, quantile: float,
        start: datetime, end: datetime, step: str = "1m",
    ) -> list[list[float]]:
        svc = self._esc(service)
        results = self._range_safe(
            (
                f'histogram_quantile({quantile}, sum by (le) '
                f'(rate(traces_service_graph_request_server_seconds_bucket'
                f'{{server="{svc}"}}[1m]))) * 1000'
            ),
            start, end, step=step,
        )
        return _series_points(results[0]["values"]) if results else []

    def get_request_rate_series(
        self, service: str, start: datetime, end: datetime, step: str = "1m"
    ) -> list[list[float]]:
        """Request rate time series for a service over [start, end]."""
        svc = self._esc(service)
        results = self._range_safe(
            f'sum(rate(traces_service_graph_request_total{{server="{svc}"}}[1m]))',
            start, end, step=step,
        )
        return _series_points(results[0]["values"]) if results else []

    def get_container_memory_pct_series(
        self, service: str, namespace: str,
        start: datetime, end: datetime, step: str = "1m",
    ) -> list[list[float]]:
        """Container memory usage % (vs limit) over [start, end] as [[ts, val]]."""
        ns, svc_re = self._esc(namespace), self._esc_re(service)
        pod_selector = self._pod_label_selector(service, namespace)
        query_label = (
            f'sum(container_memory_usage_bytes{{namespace="{ns}",container!=""}} '
            f'* on(pod) group_left() {pod_selector}) '
            f'/ sum(kube_pod_container_resource_limits{{namespace="{ns}",resource="memory"}} '
            f'* on(pod) group_left() {pod_selector}) * 100'
        )
        results = self._range_safe(query_label, start, end, step=step)
        if not results:
            query_prefix = (
                f'sum(container_memory_usage_bytes{{namespace="{ns}",pod=~"{svc_re}-.*"}}) '
                f'/ sum(kube_pod_container_resource_limits'
                f'{{namespace="{ns}",pod=~"{svc_re}-.*",resource="memory"}}) * 100'
            )
            results = self._range_safe(query_prefix, start, end, step=step)
        return _series_points(results[0]["values"]) if results else []

    def get_container_cpu_pct_series(
        self, service: str, namespace: str,
        start: datetime, end: datetime, step: str = "1m",
    ) -> list[list[float]]:
        """Container CPU usage % (vs limit) over [start, end] as [[ts, val]]."""
        ns, svc_re = self._esc(namespace), self._esc_re(service)
        pod_selector = self._pod_label_selector(service, namespace)
        query_label = (
            f'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}",container!=""}}[2m]) '
            f'* on(pod) group_left() {pod_selector}) '
            f'/ sum(kube_pod_container_resource_limits{{namespace="{ns}",resource="cpu"}} '
            f'* on(pod) group_left() {pod_selector}) * 100'
        )
        results = self._range_safe(query_label, start, end, step=step)
        if not results:
            query_prefix = (
                f'sum(rate(container_cpu_usage_seconds_total'
                f'{{namespace="{ns}",pod=~"{svc_re}-.*",container!=""}}[2m])) '
                f'/ sum(kube_pod_container_resource_limits'
                f'{{namespace="{ns}",pod=~"{svc_re}-.*",resource="cpu"}}) * 100'
            )
            results = self._range_safe(query_prefix, start, end, step=step)
        return _series_points(results[0]["values"]) if results else []

    @staticmethod
    def _pod_label_selector(service: str, namespace: str) -> str:
        # service/namespace are exact label values here → string-escape (M11).
        svc, ns = PrometheusClient._esc(service), PrometheusClient._esc(namespace)
        return (
            f'(kube_pod_labels{{namespace="{ns}",label_app="{svc}"}} or '
            f'kube_pod_labels{{namespace="{ns}",label_app_kubernetes_io_name="{svc}"}} or '
            f'kube_pod_labels{{namespace="{ns}",label_k8s_app="{svc}"}})'
        )
