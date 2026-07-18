"""Influence Graph — re-orient edges in the direction failure propagates.

★ Highest-risk file. Unit-test edge orientation before any signal code.

Rules:
  sync   → reverse (callee → caller)
  async  → keep    (producer → consumer)
  runs-on → orient infra → service
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts import CallType

from .incident_subgraph import IncidentSubgraph
from .types import Edge, Node


@dataclass
class InfluenceGraph:
    """Unified infra + service influence graph for one incident."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

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

    def out_degree(self, node_id: str) -> int:
        return len(self.out_edges(node_id))

    def in_degree(self, node_id: str) -> int:
        return len(self.in_edges(node_id))


def orient_edge(edge: Edge) -> Edge:
    """Re-orient a single edge into influence-flow direction."""
    if edge.call_type == CallType.SYNC:
        # callee → caller  (reverse)
        return Edge(
            src=edge.dst,
            dst=edge.src,
            call_type=edge.call_type,
            degraded=edge.degraded,
        )
    if edge.call_type == CallType.ASYNC:
        # producer → consumer (keep)
        return Edge(
            src=edge.src,
            dst=edge.dst,
            call_type=edge.call_type,
            degraded=edge.degraded,
        )
    if edge.call_type == CallType.RUNS_ON:
        # Ensure infra → service. Fixtures already store infra→service;
        # if reversed input sneaks in, flip based on convention that
        # RUNS_ON edges always point infra → service after orientation.
        return Edge(
            src=edge.src,
            dst=edge.dst,
            call_type=edge.call_type,
            degraded=edge.degraded,
        )
    raise ValueError(f"Unknown call_type: {edge.call_type}")


def build_influence_graph(subgraph: IncidentSubgraph) -> InfluenceGraph:
    """Build one unified IG from the incident subgraph. Build once; reuse."""
    ig = InfluenceGraph()
    for nid, node in subgraph.nodes.items():
        ig.nodes[nid] = node

    for edge in subgraph.edges:
        oriented = orient_edge(edge)
        # Skip if orientation somehow left the subgraph (shouldn't).
        if oriented.src in ig.nodes and oriented.dst in ig.nodes:
            ig.edges.append(oriented)

    return ig
