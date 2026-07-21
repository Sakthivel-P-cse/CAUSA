"""
contracts/telemetry.py — Causa contract C1: the normalized telemetry types.

This module is the SHARED SEAM between Dev A (produces), Dev B (graphs/scoring),
and Dev C (eval/classification). It owns the normalized telemetry primitives;
every consumer imports these types FROM here. The dependency points upstream
into the contract, never the other way — that inversion is what makes ingestion
a loosely-coupled, drop-in module.

Rules for this file (enforced by review, per Team_Split §3):
  • stdlib imports ONLY — no dependency on any client, RCA, or remediation concept.
  • Changes require all three devs' approval (it is a cross-dev seam).

PRD2 §4 / RCA_Engine_Spec §1.2:
  • Typed telemetry envelope (signal-specific): spans / metrics / logs kept distinct.
  • Causality-safe ordering (trace DAG > logical clock > wall-clock fallback).
  • Edge `call_type` (sync|async|runs-on) so Dev B can build the Influence Graph.
  • Raw telemetry kept as references, not copied.

Timestamp convention: every `datetime` produced by ingestion is timezone-AWARE
UTC (`datetime.fromtimestamp(x, tz=timezone.utc)`). Naive datetimes are a bug —
they silently break S3 clock-skew ordering and anomaly windowing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol


# ─────────────────────────────────────────────────────────────
# Enums: span kind + edge call semantics
# ─────────────────────────────────────────────────────────────

class SpanKind(str, Enum):
    """OTLP span kind. Determines caller/callee role and sync/async hints."""

    INTERNAL = "internal"
    SERVER   = "server"
    CLIENT   = "client"
    PRODUCER = "producer"
    CONSUMER = "consumer"

    # OTLP/JSON encodes kind as an int enum (or a SPAN_KIND_* string). Map both.
    @classmethod
    def from_otlp(cls, raw: Any) -> "SpanKind":
        int_map = {
            0: cls.INTERNAL,   # UNSPECIFIED → treat as internal
            1: cls.INTERNAL,
            2: cls.SERVER,
            3: cls.CLIENT,
            4: cls.PRODUCER,
            5: cls.CONSUMER,
        }
        if isinstance(raw, bool):  # guard: bool is an int subclass
            return cls.INTERNAL
        if isinstance(raw, int):
            return int_map.get(raw, cls.INTERNAL)
        if isinstance(raw, str):
            s = raw.strip()
            # OTLP/JSON sometimes encodes the int kind AS a numeric string
            # ("3"); map it through the int table instead of dropping to
            # INTERNAL (which silently mis-orients CLIENT/SERVER edges).
            if s.lstrip("-").isdigit():
                return int_map.get(int(s), cls.INTERNAL)
            s = s.upper().replace("SPAN_KIND_", "")
            try:
                return cls(s.lower())
            except ValueError:
                return cls.INTERNAL
        return cls.INTERNAL


class CallType(str, Enum):
    """
    Direction failure influence flows along an edge (RCA_Engine_Spec §1.2).

    Dev B re-orients edges into the Influence Graph by this field:
      • sync    — callee → caller (a slow/broken B slows A): REVERSE in IG.
      • async   — producer → consumer (a bad producer poisons the consumer): KEEP.
      • runs-on — infra → service: orient infra → service.
    """

    SYNC    = "sync"
    ASYNC   = "async"
    RUNS_ON = "runs-on"


# ─────────────────────────────────────────────────────────────
# Normalized graph primitives (the module's core output types)
# ─────────────────────────────────────────────────────────────

@dataclass
class TraceSpan:
    """A single span from a Tempo trace (trace-execution graph)."""

    span_id:        str
    parent_span_id: str | None
    service:        str
    operation:      str
    duration_ms:    float
    status_code:    int              # 0=unset, 1=ok, 2=error
    error_message:  str | None
    start_time:     datetime         # timezone-aware UTC
    # Span kind + attributes are what let ingestion derive edge call_type.
    # Without them the Influence Graph cannot be oriented (RCA_Engine_Spec §1.2).
    kind:           SpanKind = SpanKind.INTERNAL
    attributes:     dict[str, str] = field(default_factory=dict)


@dataclass
class ServiceEdge:
    """A directed call edge between two services (service-dependency graph)."""

    caller:         str
    callee:         str
    request_rate:   float            # requests/sec (traces_service_graph_request_total)
    error_rate:     float            # 0.0 – 1.0 (fraction of requests failing)
    latency_p99_ms: float            # milliseconds
    # sync (default), async, or runs-on — set from span kind/attributes. Dev B
    # reverses sync edges to build the Influence Graph.
    call_type:      CallType = CallType.SYNC


@dataclass(frozen=True)
class Edge:
    """
    A single topology edge — the element type of the C1 `topology()` adapter.

    Unifies service call edges (sync/async) and infra `runs-on` edges in ONE
    list, as RCA_Engine_Spec §1.2 requires (infra + service in the same graph).
    """

    src:       str
    dst:       str
    call_type: CallType


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


# ─────────────────────────────────────────────────────────────
# Causality + provenance
# ─────────────────────────────────────────────────────────────

class OrderingSource(str, Enum):
    """How a span's causal position was derived (PRD2 §4 causality hierarchy)."""

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
# Query window (input to the C1 adapters)
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Window:
    """A closed time interval for a query. Both bounds timezone-aware UTC."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Window bounds must be timezone-aware (UTC).")

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


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
    # Which tier of the causality hierarchy fixed this span's ordering.
    ordering_source: OrderingSource | None = None
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


@dataclass(frozen=True)
class LogRecord:
    """Narrow C1 log shape for classification (Dev C)."""

    timestamp: datetime
    node_id: str
    severity: str
    message: str
    attributes: dict[str, str] = field(default_factory=dict)


class LogsPort(Protocol):
    def logs(
        self,
        node_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[LogRecord]:
        ...


TelemetryAdapters = LogsPort


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
    # Unified topology (service sync/async + infra runs-on), for Dev B's IG.
    edges: list[Edge] = field(default_factory=list)
    references: list[TelemetryReference] = field(default_factory=list)

    @classmethod
    def from_fixture(cls, data: dict[str, Any]) -> "IncidentTelemetry":
        """
        Build from the golden-fixture JSON shape so the fixture round-trips
        through this type (not a shape the contract can't describe). `edges` are
        coerced to Edge/CallType and ISO datetimes are parsed; the fixture's
        `services` convenience list is ignored (the node set is the union of edge
        endpoints).
        """
        def _dt(v: Any) -> datetime:
            return datetime.fromisoformat(v.replace("Z", "+00:00")) if isinstance(v, str) else v

        edges = [
            Edge(src=e["src"], dst=e["dst"], call_type=CallType(e["call_type"]))
            for e in data.get("edges", [])
        ]
        return cls(
            incident_id=data["incident_id"],
            alerted_service=data["alerted_service"],
            window_start=_dt(data["window_start"]),
            window_end=_dt(data["window_end"]),
            trace_id=data.get("trace_id"),
            edges=edges,
        )

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
            "topology_edge_count": len(self.edges),
            "infra_node_count": len(self.infra_nodes),
            "metric_point_count": len(self.metric_points),
            "log_record_count": len(self.log_records),
            "reference_count": len(self.references),
        }
