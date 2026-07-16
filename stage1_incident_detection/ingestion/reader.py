"""
reader.py — TelemetryReader: OTel backends → typed IncidentTelemetry.

This is the module's single entry point. A consumer constructs a
TelemetryReader with backend clients and calls read_incident_window();
it gets back one fully-normalized IncidentTelemetry — spans, service
edges, infra nodes, metric series, and logs kept as distinct typed
envelopes, with backend references for the raw payloads.

PRD2 §8: "Typed envelope reading from the OTel stack (Tempo/Loki/Prom)."
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .clients.loki_client import LokiClient
from .clients.prometheus_client import PrometheusClient
from .clients.tempo_client import TempoClient
from .contract import (
    IncidentTelemetry,
    LogRecordEnvelope,
    MetricPointEnvelope,
    SpanEnvelope,
    TelemetryReference,
)
from .contract import TraceSpan

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _enrich_spans_with_dag(
    spans: list[TraceSpan],
    trace_id: str,
) -> list[SpanEnvelope]:
    """
    Attach causal DAG depth and sibling logical_seq to each span.
    Depth 0 = root. logical_seq orders children of the same parent by wall time.

    This is the trace-DAG tier of the PRD2 §4 causality hierarchy: it gives
    the consumer a causal position per span without it having to re-walk the
    parent/child structure.
    """
    by_id = {s.span_id: s for s in spans if s.span_id}
    children: dict[str | None, list[TraceSpan]] = {}
    for span in spans:
        parent = span.parent_span_id if span.parent_span_id in by_id else None
        children.setdefault(parent, []).append(span)

    for siblings in children.values():
        siblings.sort(key=lambda s: s.start_time)

    depth_cache: dict[str, int] = {}

    def depth_of(span_id: str) -> int:
        if span_id in depth_cache:
            return depth_cache[span_id]
        span = by_id.get(span_id)
        if span is None:
            return 0
        parent = span.parent_span_id
        if not parent or parent not in by_id:
            depth_cache[span_id] = 0
            return 0
        d = depth_of(parent) + 1
        depth_cache[span_id] = d
        return d

    envelopes: list[SpanEnvelope] = []
    for parent_key, siblings in children.items():
        for seq, span in enumerate(siblings):
            ref = TelemetryReference(
                backend="tempo",
                kind="trace",
                locator=trace_id,
            )
            envelopes.append(
                SpanEnvelope(
                    span=span,
                    trace_id=trace_id,
                    dag_depth=depth_of(span.span_id),
                    logical_seq=seq,
                    reference=ref,
                )
            )
    return envelopes


class TelemetryReader:
    """
    Single entry point to pull typed incident telemetry from the Stage-1 stack.

    Keeps spans / metrics / logs as distinct envelope types and stores only
    backend references for raw payloads (never copies raw telemetry around).
    """

    def __init__(
        self,
        prom: PrometheusClient,
        tempo: TempoClient,
        loki: LokiClient | None = None,
    ):
        self._prom = prom
        self._tempo = tempo
        self._loki = loki

    # ── Convenience constructor ───────────────────────────────────────

    @classmethod
    def from_defaults(
        cls,
        prometheus_url: str = "http://prometheus.prometheus.svc.cluster.local:9090",
        tempo_url: str = "http://tempo.tempo.svc.cluster.local:3200",
        loki_url: str | None = "http://loki.loki.svc.cluster.local:3100",
    ) -> "TelemetryReader":
        """Build a reader wired to the in-cluster Stage-1 backends."""
        return cls(
            prom=PrometheusClient(prometheus_url),
            tempo=TempoClient(tempo_url),
            loki=LokiClient(loki_url) if loki_url else None,
        )

    def close(self) -> None:
        self._prom.close()
        self._tempo.close()
        if self._loki is not None:
            self._loki.close()

    def resolve_trace_id(
        self,
        service: str | None,
        preferred: str | None = None,
        lookback_minutes: int = 10,
    ) -> str | None:
        if preferred:
            return preferred
        ids = self._tempo.search_error_traces(
            service=service,
            lookback_minutes=lookback_minutes,
            limit=1,
        )
        if ids:
            return ids[0]
        ids = self._tempo.search_traces(
            service=service,
            lookback_minutes=lookback_minutes,
            limit=1,
        )
        return ids[0] if ids else None

    def read_incident_window(
        self,
        *,
        incident_id: str,
        alerted_service: str,
        around: datetime,
        lookback_minutes: int = 10,
        namespace: str | None = None,
        preferred_trace_id: str | None = None,
        include_logs: bool = True,
        include_metrics: bool = True,
    ) -> IncidentTelemetry:
        """
        Load typed envelopes for one incident window from Tempo/Prom/Loki.

        This is the normalization boundary: every consumer downstream reads
        the returned IncidentTelemetry, not raw backend responses.
        """
        window_end = around + timedelta(minutes=1)
        window_start = around - timedelta(minutes=lookback_minutes)
        telemetry = IncidentTelemetry(
            incident_id=incident_id,
            alerted_service=alerted_service,
            window_start=window_start,
            window_end=window_end,
        )

        # ── Traces ────────────────────────────────────────────────────
        trace_id = self.resolve_trace_id(
            service=alerted_service,
            preferred=preferred_trace_id,
            lookback_minutes=lookback_minutes,
        )
        telemetry.trace_id = trace_id
        if trace_id:
            try:
                spans = self._tempo.get_trace(trace_id)
                telemetry.spans = _enrich_spans_with_dag(spans, trace_id)
                telemetry.references.append(
                    TelemetryReference(
                        backend="tempo",
                        kind="trace",
                        locator=trace_id,
                        window_start=window_start,
                        window_end=window_end,
                    )
                )
            except Exception as exc:
                logger.warning("Failed to load trace %s: %s", trace_id, exc)

        # ── Service graph edges (metrics-derived, but edge-shaped) ────
        try:
            edges = self._prom.get_service_edges()
            telemetry.service_edges = edges
            telemetry.references.append(
                TelemetryReference(
                    backend="prometheus",
                    kind="promql",
                    locator="traces_service_graph_request_total",
                    window_start=window_start,
                    window_end=window_end,
                )
            )
        except Exception as exc:
            logger.warning("Failed to load service edges: %s", exc)

        # ── Infra nodes ───────────────────────────────────────────────
        try:
            telemetry.infra_nodes = self._prom.get_infra_nodes()
            telemetry.references.append(
                TelemetryReference(
                    backend="prometheus",
                    kind="promql",
                    locator="kube_pod_info|node_cpu|node_memory",
                    window_start=window_start,
                    window_end=window_end,
                )
            )
        except Exception as exc:
            logger.warning("Failed to load infra nodes: %s", exc)

        # ── Per-service metric series ─────────────────────────────────
        if include_metrics:
            services = {alerted_service}
            for edge in telemetry.service_edges:
                if edge.caller == alerted_service or edge.callee == alerted_service:
                    services.add(edge.caller)
                    services.add(edge.callee)
            for env in telemetry.spans:
                services.add(env.span.service)

            for svc in sorted(s for s in services if s and s != "unknown"):
                self._append_metric_series(telemetry, svc, "error_rate", lookback_minutes)
                self._append_metric_series(telemetry, svc, "latency_p99_ms", lookback_minutes)
                self._append_metric_series(telemetry, svc, "request_rate", lookback_minutes)
                if namespace:
                    self._append_container_series(
                        telemetry, svc, namespace, "cpu_pct", lookback_minutes
                    )
                    self._append_container_series(
                        telemetry, svc, namespace, "memory_pct", lookback_minutes
                    )

        # ── Logs ──────────────────────────────────────────────────────
        if include_logs and self._loki is not None:
            try:
                raw_logs = self._loki.get_logs(
                    service=alerted_service,
                    level="error",
                    lookback_minutes=lookback_minutes,
                    limit=50,
                )
                for entry in raw_logs:
                    telemetry.log_records.append(
                        LogRecordEnvelope(
                            service=str(entry.get("service") or alerted_service),
                            message=str(entry.get("message") or ""),
                            level=str(entry.get("level") or "unknown"),
                            timestamp=_parse_iso(entry.get("timestamp")),
                            labels=dict(entry.get("labels") or {}),
                            reference=TelemetryReference(
                                backend="loki",
                                kind="logql",
                                locator=f'service="{alerted_service}" level=error',
                                window_start=window_start,
                                window_end=window_end,
                            ),
                        )
                    )
                if raw_logs:
                    telemetry.references.append(
                        TelemetryReference(
                            backend="loki",
                            kind="logql",
                            locator=f'service="{alerted_service}"',
                            window_start=window_start,
                            window_end=window_end,
                        )
                    )
            except Exception as exc:
                logger.warning("Failed to load logs for %s: %s", alerted_service, exc)

        logger.info("IncidentTelemetry loaded: %s", telemetry.to_summary())
        return telemetry

    def _append_metric_series(
        self,
        telemetry: IncidentTelemetry,
        service: str,
        metric: str,
        lookback_minutes: int,
    ) -> None:
        try:
            if metric == "error_rate":
                series = self._prom.get_error_rate_series(service, window_minutes=lookback_minutes)
                locator = f'error_rate{{server="{service}"}}'
            elif metric == "latency_p99_ms":
                series = self._prom.get_latency_p99_series(service, window_minutes=lookback_minutes)
                locator = f'latency_p99{{server="{service}"}}'
            else:
                series = self._prom.get_request_rate_series(service, window_minutes=lookback_minutes)
                locator = f'request_rate{{server="{service}"}}'
        except Exception as exc:
            logger.debug("metric %s/%s failed: %s", service, metric, exc)
            return

        if not series:
            return

        value = float(series[-1][1])
        ts = datetime.utcfromtimestamp(series[-1][0])
        ref = TelemetryReference(
            backend="prometheus",
            kind="promql",
            locator=locator,
            window_start=telemetry.window_start,
            window_end=telemetry.window_end,
        )
        telemetry.metric_points.append(
            MetricPointEnvelope(
                service=service,
                metric=metric,
                value=value,
                timestamp=ts,
                series=series,
                reference=ref,
            )
        )
        telemetry.references.append(ref)

    def _append_container_series(
        self,
        telemetry: IncidentTelemetry,
        service: str,
        namespace: str,
        metric: str,
        lookback_minutes: int,
    ) -> None:
        try:
            if metric == "cpu_pct":
                values = self._prom.get_container_cpu_pct_series(
                    service, namespace, window_minutes=lookback_minutes
                )
            else:
                values = self._prom.get_container_memory_pct_series(
                    service, namespace, window_minutes=lookback_minutes
                )
        except Exception as exc:
            logger.debug("container metric %s/%s failed: %s", service, metric, exc)
            return

        if not values:
            return

        # Synthesize a time series from values (no absolute timestamps from client)
        end = _utcnow().replace(tzinfo=None)
        series = [
            [(end - timedelta(minutes=len(values) - i)).timestamp(), float(v)]
            for i, v in enumerate(values)
        ]
        ref = TelemetryReference(
            backend="prometheus",
            kind="promql",
            locator=f'{metric}{{service="{service}",namespace="{namespace}"}}',
            window_start=telemetry.window_start,
            window_end=telemetry.window_end,
        )
        telemetry.metric_points.append(
            MetricPointEnvelope(
                service=service,
                metric=metric,
                value=float(values[-1]),
                labels={"namespace": namespace},
                series=series,
                reference=ref,
            )
        )
        telemetry.references.append(ref)
