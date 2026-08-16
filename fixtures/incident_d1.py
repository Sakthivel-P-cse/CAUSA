"""Fixture D1 — two disjoint faults (no shared deps).

Stage 1 connected components should yield cluster count 2.
Synthetic stand-in until Dev A dual-injection chaos lands.
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


def load_incident_d1() -> tuple[Topology, tuple, AnomalyOutput, GroundTruth]:
    nodes = (
        Node("a-svc", NodeKind.SERVICE, "a-svc"),
        Node("a-db", NodeKind.DEPENDENCY, "a-db"),
        Node("b-svc", NodeKind.SERVICE, "b-svc"),
        Node("b-db", NodeKind.DEPENDENCY, "b-db"),
    )
    edges = (
        Edge("a-svc", "a-db", CallType.SYNC, degraded=True),
        Edge("b-svc", "b-db", CallType.SYNC, degraded=True),
    )
    topology = Topology(nodes=nodes, edges=edges)
    traces: tuple = ()
    anomaly = AnomalyOutput(
        anomaly_score={"a-db": 0.9, "a-svc": 0.7, "b-db": 0.88, "b-svc": 0.65},
        first_deviation_time={
            "a-db": 0.0,
            "a-svc": 2.0,
            "b-db": 0.5,
            "b-svc": 2.5,
        },
        resource_deviation={
            "a-db": True,
            "a-svc": False,
            "b-db": True,
            "b-svc": False,
        },
    )
    gt = GroundTruth(
        incident_id="d1-disjoint",
        true_root_node="a-db",
        fault_class="Dependency",
        inject_start=0.0,
        inject_end=30.0,
        expected_cluster_count=2,
        origin_nodes=frozenset({"a-db", "b-db"}),
    )
    return topology, traces, anomaly, gt
