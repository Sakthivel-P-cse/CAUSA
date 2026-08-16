"""Fixture D2 — two faults sharing a downstream dependency.

Connected via shared-db so Stage 1 yields one component; Stage 3 origin
clustering must split to cluster count 2 (onsets differ by more than Δ,
no causal reachability between origins).
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


def load_incident_d2() -> tuple[Topology, tuple, AnomalyOutput, GroundTruth]:
    nodes = (
        Node("svc-a", NodeKind.SERVICE, "svc-a"),
        Node("svc-b", NodeKind.SERVICE, "svc-b"),
        Node("shared-db", NodeKind.DEPENDENCY, "shared-db"),
    )
    # Both services call the same downstream dep.
    edges = (
        Edge("svc-a", "shared-db", CallType.SYNC, degraded=True),
        Edge("svc-b", "shared-db", CallType.SYNC, degraded=True),
    )
    topology = Topology(nodes=nodes, edges=edges)
    traces: tuple = ()
    # Independent app faults on svc-a / svc-b; shared-db lights up late as victim.
    # Onset gap (12s) > default Δ (5s); neither origin reaches the other in IG.
    anomaly = AnomalyOutput(
        anomaly_score={"svc-a": 0.95, "svc-b": 0.93, "shared-db": 0.50},
        first_deviation_time={"svc-a": 0.0, "svc-b": 12.0, "shared-db": 20.0},
        resource_deviation={"svc-a": True, "svc-b": True, "shared-db": False},
    )
    gt = GroundTruth(
        incident_id="d2-shared-dep",
        true_root_node="svc-a",
        fault_class="AppLogic",
        inject_start=0.0,
        inject_end=40.0,
        expected_cluster_count=2,
        origin_nodes=frozenset({"svc-a", "svc-b"}),
    )
    return topology, traces, anomaly, gt
