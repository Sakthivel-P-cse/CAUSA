"""
adapters.py — the C1 telemetry adapter surface (Team_Split §3, contract C1).

A read API over the OTel stack with the four canonical signatures Dev B and
Dev C consume:

    traces(window)            -> list[SpanEnvelope]      (DAG-ordered)
    metrics(node, m, window)  -> list[[ts, value]]
    logs(node, window)        -> list[LogRecordEnvelope]
    topology(window?)         -> list[Edge]              (sync|async|runs-on)

The reader's one-shot `read_incident_window()` bundle is built ON TOP of these
(see reader.py). Consumers may use either the fine-grained adapters or the
bundle — both go through this single normalization boundary.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from contracts import (
    Edge,
    LogRecordEnvelope,
    MetricPointEnvelope,
    SpanEnvelope,
    TelemetryReference,
    Window,
)

from .clients.loki_client import LokiClient
from .clients.prometheus_client import PrometheusClient
from .clients.tempo_client import TempoClient
from .ordering import enrich_spans_with_dag
from .topology import build_topology, stamp_service_edges

logger = logging.getLogger(__name__)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _step_seconds(window: Window, target_points: int = 120) -> int:
    """
    A step derived purely from the window so EVERY metric for the same window
    lands on the SAME Prometheus grid. Alignment is what lets anomaly_series
    union metrics without interleaving (score.py) — a per-metric step offset
    would leave FirstDeviationTime's consecutive-run check unable to sustain
    (audit #2b). Floored at 15s; target ~`target_points` samples.
    """
    secs = max(1.0, (window.end - window.start).total_seconds())
    return max(15, int(secs // target_points) or 15)


def _step_str(window: Window) -> str:
    return f"{_step_seconds(window)}s"


class TelemetryAdapter:
    """
    The C1 read API. Owns the backend clients; every method returns typed,
    normalized rows — never raw HTTP responses.
    """

    def __init__(
        self,
        prom: PrometheusClient,
        tempo: TempoClient,
        loki: LokiClient | None = None,
        *,
        namespace: str | None = None,
    ):
        self._prom = prom
        self._tempo = tempo
        self._loki = loki
        # Container CPU/mem series (cpu, saturation) are namespaced; the incident
        # namespace is set here so metrics(node, "cpu", ...) can resolve them.
        self._namespace = namespace

    @classmethod
    def from_defaults(
        cls,
        prometheus_url: str = "http://prometheus.prometheus.svc.cluster.local:9090",
        tempo_url: str = "http://tempo.tempo.svc.cluster.local:3200",
        loki_url: str | None = "http://loki.loki.svc.cluster.local:3100",
        namespace: str | None = None,
    ) -> "TelemetryAdapter":
        return cls(
            prom=PrometheusClient(prometheus_url),
            tempo=TempoClient(tempo_url),
            loki=LokiClient(loki_url) if loki_url else None,
            namespace=namespace,
        )

    def close(self) -> None:
        self._prom.close()
        self._tempo.close()
        if self._loki is not None:
            self._loki.close()

    # ── C1: traces(window) ─────────────────────────────────────────────

    def traces(
        self, window: Window, service: str | None = None, limit: int = 20
    ) -> list[SpanEnvelope]:
        """
        All spans for the traces touching `window`, DAG-ordered. Searches Tempo
        for traces in the window, fetches each, and enriches spans with causal
        position (ordering.py).
        """
        trace_ids = self._tempo.search_traces_in_window(
            service=service, start=window.start, end=window.end, limit=limit
        )
        envelopes: list[SpanEnvelope] = []
        for tid in trace_ids:
            try:
                spans = self._tempo.get_trace(tid)
            except Exception as exc:
                logger.warning("Failed to load trace %s: %s", tid, exc)
                continue
            envelopes.extend(enrich_spans_with_dag(spans, tid))
        return envelopes

    # ── C1: metrics(node, metric, window) ──────────────────────────────

    def metrics(
        self, node: str, metric: str, window: Window, namespace: str | None = None
    ) -> list[list[float]]:
        """
        Return the [[unix_ts, value], ...] series for one (node, metric).

        Metric names follow the anomaly contract's METRICS plus the RED extras:
          p95_latency | latency_p99_ms | error_rate | request_rate
          cpu | saturation            (container CPU% / memory-vs-limit%; namespaced)
          retry_rate                  (not first-class on the OTel Demo → []; a
                                       documented gap, never synthesized)

        Queries honor the ABSOLUTE [window.start, window.end] and share one
        window-derived step, so a warmup baseline reads its historical healthy
        window (not "now") and every metric aligns on one grid (audit #2).
        """
        start, end = window.start, window.end
        step = _step_str(window)
        ns = namespace or self._namespace

        if metric == "error_rate":
            return self._prom.get_error_rate_series(node, start, end, step)
        if metric == "p95_latency":
            return self._prom.get_latency_p95_series(node, start, end, step)
        if metric == "latency_p99_ms":
            return self._prom.get_latency_p99_series(node, start, end, step)
        if metric == "request_rate":
            return self._prom.get_request_rate_series(node, start, end, step)
        if metric == "cpu":
            if not ns:
                return []
            return self._prom.get_container_cpu_pct_series(node, ns, start, end, step)
        if metric == "saturation":
            if not ns:
                return []
            return self._prom.get_container_memory_pct_series(node, ns, start, end, step)
        if metric == "retry_rate":
            # No first-class retry counter on the OTel Demo out of the box.
            # Left as a documented gap; anomaly's max-composite tolerates it.
            return []
        logger.debug("Unknown metric requested: %s", metric)
        return []

    def metric_point(
        self, node: str, metric: str, window: Window, namespace: str | None = None
    ) -> MetricPointEnvelope | None:
        """metrics() wrapped as a MetricPointEnvelope (latest value + full series)."""
        series = self.metrics(node, metric, window, namespace=namespace)
        if not series:
            return None
        return MetricPointEnvelope(
            service=node,
            metric=metric,
            value=float(series[-1][1]),
            timestamp=datetime.fromtimestamp(series[-1][0], tz=timezone.utc),
            series=series,
            reference=TelemetryReference(
                backend="prometheus", kind="promql", locator=f'{metric}{{server="{node}"}}',
                window_start=window.start, window_end=window.end,
            ),
        )

    # ── C1: logs(node, window) ─────────────────────────────────────────

    def logs(
        self, node: str, window: Window, level: str | None = "error", limit: int = 50
    ) -> list[LogRecordEnvelope]:
        """Error-level (by default) log records for a service in the window."""
        if self._loki is None:
            return []
        try:
            raw = self._loki.get_logs(
                service=node, level=level, limit=limit,
                start=window.start, end=window.end,
            )
        except Exception as exc:
            logger.warning("Failed to load logs for %s: %s", node, exc)
            return []
        ref = TelemetryReference(
            backend="loki", kind="logql", locator=f'service="{node}" level={level}',
            window_start=window.start, window_end=window.end,
        )
        return [
            LogRecordEnvelope(
                service=str(e.get("service") or node),
                message=str(e.get("message") or ""),
                level=str(e.get("level") or "unknown"),
                timestamp=_parse_iso(e.get("timestamp")),
                labels=dict(e.get("labels") or {}),
                reference=ref,
            )
            for e in raw
        ]

    # ── C1: topology(window?) ──────────────────────────────────────────

    def topology(self, window: Window | None = None, service: str | None = None) -> list[Edge]:
        """
        The unified [Edge] topology: service call edges (sync|async) + infra
        runs-on edges. When a `window` is given, edge call_type is derived from
        that window's spans; otherwise service edges default to sync.
        """
        service_edges = self._prom.get_service_edges()
        if window is not None:
            spans = [e.span for e in self.traces(window, service=service)]
            stamp_service_edges(service_edges, spans)
        infra_nodes = self._prom.get_infra_nodes()
        return build_topology(service_edges, infra_nodes)
