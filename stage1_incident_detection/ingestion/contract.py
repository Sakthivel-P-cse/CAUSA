"""
contract.py — TraceOps Stage-1 ingestion public contract.

This module OWNS the normalized telemetry types. Downstream consumers
(RCA engine, dashboard, anything else) import these types FROM here —
the dependency points upstream into ingestion, never the other way.
That inversion is what makes Stage-1 a loosely-coupled, drop-in module.

PRD2 §4:
  • Typed telemetry envelope (signal-specific)
  • spans / metric points / log records kept distinct
  • Causality-safe ordering (trace DAG > logical clock > wall-clock fallback)
  • Storage: raw telemetry kept as references, not copied around

Nothing here depends on any RCA/remediation concept. These are pure
telemetry primitives + the envelope that wraps one incident window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ─────────────────────────────────────────────────────────────
# Normalized graph primitives (the module's core output types)
# ─────────────────────────────────────────────────────────────

@dataclass
class ServiceEdge:
    """A directed call edge between two services (service-dependency graph)."""

    caller:         str
    callee:         str
    request_rate:   float   # requests/sec (traces_service_graph_request_total)
    error_rate:     float   # 0.0 – 1.0 (fraction of requests failing)
    latency_p99_ms: float   # milliseconds


@dataclass
class InfraNode:
    """Infrastructure node/pod/container state at incident time."""

    name:              str            # node name or pod name
    kind:              str            # "node" | "pod" | "container"
    namespace:         str | None
    cpu_usage_pct:     float | None
    memory_usage_pct:  float | None
    restart_count:     int | None
    condition:         str | None     # "Ready" | "MemoryPressure" | "Running" | ...
    host_node:         str | None = None
    service_name:      str | None = None


@dataclass
class TraceSpan:
    """A single span from a Tempo trace (trace-execution graph)."""

    span_id:        str
    parent_span_id: str | None
    service:        str
    operation:      str
    duration_ms:    float
    status_code:    int        # 0=unset, 1=ok, 2=error
    error_message:  str | None
    start_time:     datetime


# ─────────────────────────────────────────────────────────────
# Causality + provenance
# ─────────────────────────────────────────────────────────────

class OrderingSource(str, Enum):
    """How a FirstDeviationTime was derived (PRD2 §4 causality hierarchy)."""

    TRACE_DAG     = "trace_dag"
    LOGICAL_CLOCK = "logical_clock"
    WALL_CLOCK    = "wall_clock"
    FALLBACK      = "fallback"


@dataclass(frozen=True)
class TelemetryReference:
    """
    Pointer to raw telemetry in its backend store. Raw payloads are never
    copied into the incident state DB — only these references are kept, so
    a consumer can re-fetch the full data on demand.
    """

    backend: str                        # "tempo" | "prometheus" | "loki"
    kind: str                           # "trace" | "promql" | "logql" | "search"
    locator: str                        # trace_id, query string, or selector
    window_start: datetime | None = None
    window_end: datetime | None = None


# ─────────────────────────────────────────────────────────────
# Signal-specific envelopes (kept distinct, PRD2 §4)
# ─────────────────────────────────────────────────────────────

@dataclass
class SpanEnvelope:
    """One span, distinct from metric/log signals, with causal position."""

    span: TraceSpan
    trace_id: str
    # Causal position in the trace DAG (0 = root). None if unknown.
    dag_depth: int | None = None
    # Sibling index among children of the same parent (logical order).
    logical_seq: int | None = None
    reference: TelemetryReference | None = None


@dataclass
class MetricPointEnvelope:
    """One metric sample or series summary, kept distinct from spans/logs."""

    service: str
    metric: str                 # e.g. error_rate, latency_p99_ms, request_rate
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: datetime | None = None
    # Full series when available: [[unix_ts, value], ...]
    series: list[list[float]] = field(default_factory=list)
    reference: TelemetryReference | None = None


@dataclass
class LogRecordEnvelope:
    """One log record, kept distinct from spans/metrics."""

    service: str
    message: str
    level: str
    timestamp: datetime | None = None
    labels: dict[str, str] = field(default_factory=dict)
    reference: TelemetryReference | None = None


# ─────────────────────────────────────────────────────────────
# The unit of handoff: one incident window, fully normalized
# ─────────────────────────────────────────────────────────────

@dataclass
class IncidentTelemetry:
    """
    All typed signals for one incident window.

    This is the single object Stage-1 hands to any consumer. Graph builders
    and scorers read THIS — never raw HTTP responses from Tempo/Prom/Loki.
    """

    incident_id: str
    alerted_service: str
    window_start: datetime
    window_end: datetime
    trace_id: str | None = None

    spans: list[SpanEnvelope] = field(default_factory=list)
    service_edges: list[ServiceEdge] = field(default_factory=list)
    infra_nodes: list[InfraNode] = field(default_factory=list)
    metric_points: list[MetricPointEnvelope] = field(default_factory=list)
    log_records: list[LogRecordEnvelope] = field(default_factory=list)
    references: list[TelemetryReference] = field(default_factory=list)

    def spans_as_trace_spans(self) -> list[TraceSpan]:
        """Unwrap span envelopes back to bare TraceSpan objects."""
        return [e.span for e in self.spans]

    def metric_series(self, service: str, metric: str) -> list[list[float]]:
        """Return the [[ts, value], ...] series for one (service, metric), if held."""
        for point in self.metric_points:
            if point.service == service and point.metric == metric and point.series:
                return point.series
        return []

    def to_summary(self) -> dict[str, Any]:
        """Compact counts — safe to log or persist as incident state."""
        return {
            "incident_id": self.incident_id,
            "alerted_service": self.alerted_service,
            "trace_id": self.trace_id,
            "span_count": len(self.spans),
            "edge_count": len(self.service_edges),
            "infra_node_count": len(self.infra_nodes),
            "metric_point_count": len(self.metric_points),
            "log_record_count": len(self.log_records),
            "reference_count": len(self.references),
        }
