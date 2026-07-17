"""Fixture D3 — causally adjacent faults with onsets within Δ.

Two early origins sharing a victim; onset gap ≤ Δ → isolation abstains
with ``MULTI-INCIDENT — ambiguous``.
"""

from __future__ import annotations

from contracts.types import (
    AnomalyOutput,
    CallType,
    Edge,
    GroundTruth,
    Node,
    NodeKind,
    Topology,
)


def load_incident_d3() -> tuple[Topology, tuple, AnomalyOutput, GroundTruth]:
    nodes = (
        Node("db1", NodeKind.DEPENDENCY, "db1"),
        Node("svc", NodeKind.SERVICE, "svc"),
        Node("db2", NodeKind.DEPENDENCY, "db2"),
    )
    # Both DBs called by same service — after IG reverse, both DBs → svc.
    edges = (
        Edge("svc", "db1", CallType.SYNC, degraded=True),
        Edge("svc", "db2", CallType.SYNC, degraded=True),
    )
    topology = Topology(nodes=nodes, edges=edges)
    traces: tuple = ()
    anomaly = AnomalyOutput(
        anomaly_score={"db1": 0.9, "db2": 0.9, "svc": 0.8},
        first_deviation_time={"db1": 0.0, "db2": 0.5, "svc": 2.0},
        resource_deviation={"db1": True, "db2": True, "svc": True},
    )
    gt = GroundTruth(
        incident_id="d3-adjacent",
        true_root_node="db1",
        fault_class="Dependency",
        inject_start=0.0,
        inject_end=20.0,
        expected_cluster_count=0,  # abstain — no ranked clusters
        origin_nodes=frozenset({"db1", "db2"}),
        ambiguous=True,
    )
    return topology, traces, anomaly, gt
