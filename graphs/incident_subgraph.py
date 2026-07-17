"""Incident Subgraph — shrink full topology to anomalous-relevant nodes."""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts.types import AnomalyOutput, Edge, Node, Topology, Trace

from .construction import Graph

# Defaults; tunable by Dev C later via Scorer / harness.
THETA_FIRE = 0.5
THETA_LOW = 0.2


@dataclass
class IncidentSubgraph:
    """On-trigger subgraph of nodes that matter for this incident."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    # Full topology edges restricted to included nodes (call_type preserved).
    anomaly: AnomalyOutput | None = None
    theta_fire: float = THETA_FIRE
    theta_low: float = THETA_LOW

    def node_ids(self) -> set[str]:
        return set(self.nodes.keys())

    def successors(self, node_id: str) -> list[str]:
        return [e.dst for e in self.edges if e.src == node_id]

    def predecessors(self, node_id: str) -> list[str]:
        return [e.src for e in self.edges if e.dst == node_id]

    def out_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.src == node_id]

    def in_edges(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.dst == node_id]

    def as_graph(self) -> Graph:
        g = Graph()
        for n in self.nodes.values():
            g.add_node(n)
        for e in self.edges:
            g.add_edge(e)
        return g


def build_incident_subgraph(
    topology: Topology,
    traces: tuple[Trace, ...],
    anomaly: AnomalyOutput,
    *,
    theta_fire: float = THETA_FIRE,
    theta_low: float = THETA_LOW,
) -> IncidentSubgraph:
    """Include a node if ANY inclusion rule fires (spec §1.1)."""
    scores = anomaly.anomaly_score
    resource = anomaly.resource_deviation
    by_id = {n.node_id: n for n in topology.nodes}

    # Nodes that participated in anomalous traces.
    trace_participants: set[str] = set()
    for tr in traces:
        if tr.anomalous or tr.trace_id in anomaly.anomalous_trace_ids:
            for span in tr.spans:
                trace_participants.add(span.service_id)

    # Seed: fire-threshold OR resource deviation OR anomalous-trace participant.
    included: set[str] = set()
    for nid in by_id:
        score = scores.get(nid, 0.0)
        if score > theta_fire:
            included.add(nid)
        elif resource.get(nid, False):
            included.add(nid)
        elif nid in trace_participants:
            included.add(nid)

    # Connectivity expansion: neighbors of anomalous nodes using θ_low so
    # slow-burn origins aren't filtered out.
    undirected: dict[str, set[str]] = {nid: set() for nid in by_id}
    for e in topology.edges:
        if e.src in undirected and e.dst in undirected:
            undirected[e.src].add(e.dst)
            undirected[e.dst].add(e.src)

    seeds = {
        nid
        for nid in by_id
        if scores.get(nid, 0.0) > theta_low
        or resource.get(nid, False)
        or nid in trace_participants
    }
    # Expand one hop from any θ_low-anomalous / resource / trace seed,
    # but only add neighbors that themselves meet θ_low OR are already included
    # via other rules — actually per spec: "connected to an anomalous node"
    # using θ_low for the connectivity check. Include neighbors of nodes that
    # exceed θ_low.
    anomalous_for_connectivity = {
        nid for nid in by_id if scores.get(nid, 0.0) > theta_low or resource.get(nid, False)
    }
    for nid in list(anomalous_for_connectivity):
        included.add(nid)
        for nbr in undirected.get(nid, ()):
            included.add(nbr)

    # Always keep seeds from fire/trace/resource.
    included |= seeds

    nodes = {nid: by_id[nid] for nid in included if nid in by_id}
    edges = [
        e
        for e in topology.edges
        if e.src in nodes and e.dst in nodes
    ]

    return IncidentSubgraph(
        nodes=nodes,
        edges=edges,
        anomaly=anomaly,
        theta_fire=theta_fire,
        theta_low=theta_low,
    )
