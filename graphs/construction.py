"""Four-graph construction from C1 topology + traces."""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts.types import CallType, Edge, Node, NodeKind, Span, Topology, Trace


def derive_call_type(span: Span, parent: Span | None = None) -> CallType:
    """Derive call_type from span kind / attributes at construction time."""
    kind = (span.span_kind or "").upper()
    attrs = span.attributes or {}

    if attrs.get("runs_on") or attrs.get("infra.runs_on"):
        return CallType.RUNS_ON

    messaging = attrs.get("messaging.system") or attrs.get("messaging.destination")
    if messaging or kind in {"PRODUCER", "CONSUMER"}:
        return CallType.ASYNC

    return CallType.SYNC


@dataclass
class Graph:
    """Simple directed graph used by the four graph builders."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, node: Node) -> None:
        self.nodes[node.node_id] = node

    def add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)

    def successors(self, node_id: str) -> list[str]:
        return [e.dst for e in self.edges if e.src == node_id]

    def predecessors(self, node_id: str) -> list[str]:
        return [e.src for e in self.edges if e.dst == node_id]

    def out_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.src == node_id]

    def in_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.dst == node_id]


def build_infrastructure_graph(topology: Topology) -> Graph:
    """Persistent infra graph: infra nodes + runs-on edges (+ DB/cache as infra)."""
    g = Graph()
    for n in topology.nodes:
        if n.kind == NodeKind.INFRA or n.runs_on is not None:
            # Include services that have a host so the edge is representable,
            # but primary nodes of interest are INFRA.
            g.add_node(n)
        if n.kind == NodeKind.INFRA:
            g.add_node(n)

    for e in topology.edges:
        if e.call_type == CallType.RUNS_ON:
            # Ensure endpoints exist.
            src = _lookup(topology, e.src)
            dst = _lookup(topology, e.dst)
            if src:
                g.add_node(src)
            if dst:
                g.add_node(dst)
            g.add_edge(e)

    # Also include bare INFRA nodes even with no edges.
    for n in topology.nodes:
        if n.kind == NodeKind.INFRA:
            g.add_node(n)

    return g


def build_service_dependency_graph(topology: Topology) -> Graph:
    """Persistent service dependency graph: logical services + sync/async edges."""
    g = Graph()
    for n in topology.nodes:
        if n.kind == NodeKind.SERVICE:
            g.add_node(n)
        # DBs that services call are useful as dependency sinks — include INFRA
        # that appear as sync/async edge endpoints.
    for e in topology.edges:
        if e.call_type in {CallType.SYNC, CallType.ASYNC}:
            src = _lookup(topology, e.src)
            dst = _lookup(topology, e.dst)
            if src:
                g.add_node(src)
            if dst:
                g.add_node(dst)
            g.add_edge(e)
    return g


def build_trace_execution_graph(traces: tuple[Trace, ...]) -> Graph:
    """On-trigger span DAG(s): one node per span, edges parent→child."""
    g = Graph()
    for trace in traces:
        by_id = {s.span_id: s for s in trace.spans}
        for span in trace.spans:
            node = Node(
                node_id=f"{trace.trace_id}:{span.span_id}",
                kind=NodeKind.SPAN,
                name=span.service_id,
            )
            g.add_node(node)
            if span.parent_span_id and span.parent_span_id in by_id:
                parent = by_id[span.parent_span_id]
                call_type = derive_call_type(span, parent)
                g.add_edge(
                    Edge(
                        src=f"{trace.trace_id}:{span.parent_span_id}",
                        dst=f"{trace.trace_id}:{span.span_id}",
                        call_type=call_type,
                    )
                )
    return g


def _lookup(topology: Topology, node_id: str) -> Node | None:
    for n in topology.nodes:
        if n.node_id == node_id:
            return n
    return None
