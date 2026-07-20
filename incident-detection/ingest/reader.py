"""
reader.py — TelemetryReader: OTel backends → typed IncidentTelemetry.

The one-shot convenience over the C1 adapters (adapters.py). A consumer calls
`read_incident_window()` and gets back one fully-normalized IncidentTelemetry —
spans (DAG-ordered), service edges (call_type-stamped), infra nodes, metric
series, logs, and the unified topology — with backend references for the raw
payloads. Everything routes through the same TelemetryAdapter normalization
boundary, so the fine-grained and bundled paths never diverge.

PRD2 §8: "Typed envelope reading from the OTel stack (Tempo/Loki/Prom)."
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from contracts import (
    IncidentTelemetry,
    TelemetryReference,
    Window,
)

from .adapters import TelemetryAdapter
from .clients.loki_client import LokiClient
from .clients.prometheus_client import PrometheusClient
from .clients.tempo_client import TempoClient
from .topology import build_topology, stamp_service_edges

logger = logging.getLogger(__name__)

# Per-service series. Uses the anomaly contract's metric names so the baselines
# and scoring downstream key on the SAME series this reader produces
# (contracts.METRICS): `p95_latency` + `error_rate` here, `cpu` + `saturation`
# when a namespace is supplied. `request_rate` is kept as RED context (not a
# scored metric); `retry_rate` has no first-class OTel-Demo counter and is a
# documented gap (adapter returns [], never synthesized).
_SERVICE_METRICS = ("error_rate", "p95_latency", "request_rate")
# Node-level resource metrics, scored under the same contract names as the
# container ones so infra flows through dev_vector unchanged. Not namespaced —
# a k8s node is cluster-scoped.
_INFRA_METRICS = ("cpu", "saturation")
_CONTAINER_METRICS = ("cpu", "saturation")


class TelemetryReader:
    """Single entry point to pull one typed incident window from the stack."""

    def __init__(
        self,
        prom: PrometheusClient,
        tempo: TempoClient,
        loki: LokiClient | None = None,
    ):
        self._prom = prom
        self._tempo = tempo
        self._loki = loki
        self._adapter = TelemetryAdapter(prom, tempo, loki)

    @classmethod
    def from_defaults(
        cls,
        prometheus_url: str = "http://prometheus.prometheus.svc.cluster.local:9090",
        tempo_url: str = "http://tempo.tempo.svc.cluster.local:3200",
        loki_url: str | None = "http://loki.loki.svc.cluster.local:3100",
    ) -> "TelemetryReader":
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
            service=service, lookback_minutes=lookback_minutes, limit=1
        )
        if ids:
            return ids[0]
        ids = self._tempo.search_traces(
            service=service, lookback_minutes=lookback_minutes, limit=1
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

        This is the normalization boundary: every consumer downstream reads the
        returned IncidentTelemetry, not raw backend responses.
        """
        window = Window(
            start=around - timedelta(minutes=lookback_minutes),
            end=around + timedelta(minutes=1),
        )
        adapter = TelemetryAdapter(self._prom, self._tempo, self._loki, namespace=namespace)

        telemetry = IncidentTelemetry(
            incident_id=incident_id,
            alerted_service=alerted_service,
            window_start=window.start,
            window_end=window.end,
        )

        # ── Traces (DAG-ordered) ──────────────────────────────────────
        telemetry.trace_id = self.resolve_trace_id(
            service=alerted_service,
            preferred=preferred_trace_id,
            lookback_minutes=lookback_minutes,
        )
        telemetry.spans = adapter.traces(window, service=alerted_service)
        # traces() samples up to `limit` recent traces and may NOT include the
        # specifically-resolved (error) trace_id, leaving the incident's headline
        # trace_id disjoint from its spans (audit L3). If it's missing, fetch that
        # trace and prepend so trace_id and spans are coherent.
        if telemetry.trace_id and telemetry.trace_id not in {
            e.trace_id for e in telemetry.spans
        }:
            telemetry.spans = adapter.trace(telemetry.trace_id) + telemetry.spans
        if telemetry.spans:
            telemetry.references.append(
                TelemetryReference(
                    backend="tempo", kind="search",
                    locator=f'service.name={alerted_service}',
                    window_start=window.start, window_end=window.end,
                )
            )
        span_objs = telemetry.spans_as_trace_spans()

        # ── Service graph edges (+ call_type from spans) ──────────────
        service_edges = self._prom.get_service_edges()
        stamp_service_edges(service_edges, span_objs)
        telemetry.service_edges = service_edges
        telemetry.references.append(
            TelemetryReference(
                backend="prometheus", kind="promql",
                locator="traces_service_graph_request_total",
                window_start=window.start, window_end=window.end,
            )
        )

        # ── Infra nodes + unified topology ────────────────────────────
        telemetry.infra_nodes = self._prom.get_infra_nodes()
        telemetry.edges = build_topology(service_edges, telemetry.infra_nodes)
        telemetry.references.append(
            TelemetryReference(
                backend="prometheus", kind="promql",
                locator="kube_pod_info|node_cpu|node_memory",
                window_start=window.start, window_end=window.end,
            )
        )

        # ── Per-service metric series ─────────────────────────────────
        if include_metrics:
            services = {alerted_service}
            for edge in telemetry.service_edges:
                if edge.caller == alerted_service or edge.callee == alerted_service:
                    services.add(edge.caller)
                    services.add(edge.callee)
            for env in telemetry.spans:
                services.add(env.span.service)

            metrics = list(_SERVICE_METRICS)
            if namespace:
                metrics += list(_CONTAINER_METRICS)

            for svc in sorted(s for s in services if s and s != "unknown"):
                for metric in metrics:
                    point = adapter.metric_point(svc, metric, window, namespace=namespace)
                    if point is not None:
                        telemetry.metric_points.append(point)
                        if point.reference:
                            telemetry.references.append(point.reference)

            # ── Per-infra-node metric series ──────────────────────────
            # Infra nodes were previously fetched for topology only (instant
            # cpu/mem via get_infra_nodes), so they entered the kernel with no
            # series → anomaly_score 0.0 and first_deviation None. Layer
            # arbitration needs both, so infra could never win on live data.
            # Query the same host names the runs-on edges use, so the anomaly
            # node IDs and the graph node IDs match.
            for host in sorted(
                {n.name for n in telemetry.infra_nodes if n.kind == "node" and n.name}
            ):
                for metric in _INFRA_METRICS:
                    point = adapter.metric_point(host, metric, window, kind="infra")
                    if point is not None:
                        telemetry.metric_points.append(point)
                        if point.reference:
                            telemetry.references.append(point.reference)

        # ── Logs ──────────────────────────────────────────────────────
        if include_logs and self._loki is not None:
            telemetry.log_records = adapter.logs(alerted_service, window, level="error")
            if telemetry.log_records:
                telemetry.references.append(
                    TelemetryReference(
                        backend="loki", kind="logql",
                        locator=f'service="{alerted_service}"',
                        window_start=window.start, window_end=window.end,
                    )
                )

        logger.info("IncidentTelemetry loaded: %s", telemetry.to_summary())
        return telemetry
