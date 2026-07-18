"""Internal graph types used behind the canonical C1 adapter boundary."""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts import CallType, NodeKind


@dataclass(frozen=True)
class Node:
    node_id: str
    kind: NodeKind
    name: str
    runs_on: str | None = None


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    call_type: CallType
    degraded: bool = False


@dataclass(frozen=True)
class Span:
    span_id: str
    service_id: str
    parent_span_id: str | None
    start_ms: float
    end_ms: float
    span_kind: str = "INTERNAL"
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Trace:
    trace_id: str
    spans: tuple[Span, ...]
    anomalous: bool = False


@dataclass(frozen=True)
class Topology:
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
