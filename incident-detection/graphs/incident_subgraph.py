"""Incident Subgraph — shrink full topology to anomalous-relevant nodes."""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts import Thresholds
from rca.types import KernelAnomaly

from .construction import Graph
from .types import Edge, Node, Topology, Trace

_DEFAULT_THRESHOLDS = Thresholds()
THETA_FIRE = _DEFAULT_THRESHOLDS.theta_fire
THETA_LOW = _DEFAULT_THRESHOLDS.theta_low


@dataclass
class IncidentSubgraph:
    """On-trigger subgraph of nodes that matter for this incident."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    # Full topology edges restricted to included nodes (call_type preserved).
    anomaly: KernelAnomaly | None = None
    theta_fire: float = THETA_FIRE
    theta_low: float = THETA_LOW
    # Nodes admitted on DIRECT deviation evidence (fire threshold, resource
    # deviation, or anomalous-trace participation) as opposed to connectivity
    # expansion. Membership is deliberately permissive for recall — a slow-burn
    # origin must not be filtered out — but that makes the node set a bad basis
    # for TEMPORAL NORMALIZATION: t0 is a min over the set, so one merely
    # ADJACENT node that happened to deviate earlier rescales T for everyone.
    # Signals normalize over these seeds; ranking still covers every node.
    seed_ids: set[str] = field(default_factory=set)

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
    anomaly: KernelAnomaly,
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

    # Direct-evidence nodes only: the θ_fire / resource / anomalous-trace rules.
    # Deliberately excludes the connectivity expansion above, whose whole point
    # is to admit nodes that show no deviation evidence of their own.
    seed_ids = {
        nid
        for nid in nodes
        if scores.get(nid, 0.0) > theta_fire
        or resource.get(nid, False)
        or nid in trace_participants
    }

    return IncidentSubgraph(
        nodes=nodes,
        edges=edges,
        anomaly=anomaly,
        theta_fire=theta_fire,
        theta_low=theta_low,
        seed_ids=seed_ids,
    )
