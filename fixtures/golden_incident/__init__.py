"""Golden incident fixture — Week 0 seam for Dev B/C offline work.

~6-node hand-authored graph with known DB origin (cart-db).
Topology + AnomalyOutput + expected RootCauseCandidate + GroundTruth.
"""

from __future__ import annotations

from contracts.types import (
    AnomalyOutput,
    AnomalyThresholds,
    CallType,
    Edge,
    GroundTruth,
    Node,
    NodeKind,
    Role,
    RootCauseCandidate,
    SignalScores,
    Span,
    Topology,
    Trace,
)

INCIDENT_ID = "golden-001"


def load_golden_incident() -> tuple[
    Topology, tuple[Trace, ...], AnomalyOutput, GroundTruth, RootCauseCandidate
]:
    """Return topology, traces, anomaly, ground truth, and expected top candidate."""
    nodes = (
        Node("frontend", NodeKind.SERVICE, "frontend"),
        Node("cart-service", NodeKind.SERVICE, "cart-service", runs_on="node-a"),
        Node("cart-db", NodeKind.DEPENDENCY, "cart-db"),
        Node("payment", NodeKind.SERVICE, "payment"),
        Node("cache", NodeKind.DEPENDENCY, "cache"),
        Node("node-a", NodeKind.INFRA, "node-a"),
    )

    edges = (
        Edge("frontend", "cart-service", CallType.SYNC, degraded=True),
        Edge("cart-service", "cart-db", CallType.SYNC, degraded=True),
        Edge("frontend", "payment", CallType.SYNC, degraded=True),
        Edge("payment", "cache", CallType.SYNC, degraded=False),
        Edge("node-a", "cart-service", CallType.RUNS_ON, degraded=False),
    )

    topology = Topology(nodes=nodes, edges=edges)

    spans = (
        Span("g1", "frontend", None, 0.0, 400.0, "SERVER"),
        Span("g2", "cart-service", "g1", 10.0, 350.0, "CLIENT",
             attributes={"peer.service": "cart-db"}),
        Span("g3", "cart-db", "g2", 20.0, 300.0, "SERVER"),
        Span("g4", "payment", "g1", 15.0, 100.0, "CLIENT"),
    )
    traces = (Trace("golden-trace", spans, anomalous=True),)

    anomaly = AnomalyOutput(
        anomaly_score={
            "cart-db": 0.95,
            "cart-service": 0.80,
            "frontend": 0.70,
            "payment": 0.45,
            "cache": 0.15,
            "node-a": 0.18,
        },
        first_deviation_time={
            "cart-db": 0.0,
            "cart-service": 2.5,
            "frontend": 4.0,
            "payment": 5.0,
            "cache": None,
            "node-a": 12.0,
        },
        resource_deviation={
            "cart-db": True,
            "cart-service": True,
            "frontend": False,
            "payment": False,
            "cache": False,
            "node-a": False,
        },
        anomalous_trace_ids=frozenset({"golden-trace"}),
        t0=0.0,
        dev_vector={
            "cart-db": {"p95_latency": 0.95, "error_rate": 0.88, "cpu": 0.40},
            "cart-service": {"p95_latency": 0.80, "error_rate": 0.55, "cpu": 0.30},
            "frontend": {"p95_latency": 0.70, "error_rate": 0.40, "cpu": 0.20},
            "payment": {"p95_latency": 0.45, "error_rate": 0.20, "cpu": 0.15},
            "cache": {"p95_latency": 0.10, "error_rate": 0.05, "cpu": 0.15},
            "node-a": {"cpu": 0.18, "saturation": 0.12},
        },
        thresholds=AnomalyThresholds(theta_fire=0.5, k_persist=3, theta_low=0.2),
    )

    ground_truth = GroundTruth(
        incident_id=INCIDENT_ID,
        true_root_node="cart-db",
        fault_class="Dependency",
        inject_start=0.0,
        inject_end=30.0,
        expected_cluster_count=1,
        origin_nodes=frozenset({"cart-db"}),
    )

    # Expected top candidate shape for Dev C harness (scores approximate;
    # structural fields are the contract — exact floats come from the scorer).
    expected_top = RootCauseCandidate(
        node_id="cart-db",
        node_kind=NodeKind.DEPENDENCY,
        score=0.0,  # filled by live scorer; fixture asserts identity/role
        confidence=0.0,
        role=Role.ORIGIN,
        signals=SignalScores(T=1.0, D=0.0, P=0.0),
        dev_vector=dict(anomaly.dev_vector["cart-db"]),
        explanation=(
            "cart-db expected origin — live scorer fills T/D/P explanation"
        ),
    )

    return topology, traces, anomaly, ground_truth, expected_top
