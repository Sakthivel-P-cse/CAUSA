"""Primary fixture S0 — clear DB origin with sync callers, slow-burn, infra host.

Topology (call direction = caller → callee):

  frontend ──sync──► cart-service ──sync──► cart-db   (origin / sync leaf)
       │                    │
       │                    └──runs-on──► node-a     (infra)
       └──sync──► payment-service ──async──► notifier  (slow-burn, mild)

Timeline (seconds from t0):
  cart-db          FirstDeviation=0.0   anomaly=0.95
  cart-service     FirstDeviation=2.0   anomaly=0.80
  frontend         FirstDeviation=4.0   anomaly=0.70
  payment-service  FirstDeviation=5.0   anomaly=0.55
  notifier         FirstDeviation=None  anomaly=0.25  (slow-burn)
  node-a           FirstDeviation=10.0  anomaly=0.20  (healthy-ish infra)
"""

from __future__ import annotations

from contracts.types import (
    AnomalyOutput,
    CallType,
    Edge,
    GroundTruth,
    Node,
    NodeKind,
    Span,
    Topology,
    Trace,
)


def load_incident_s0() -> tuple[Topology, tuple[Trace, ...], AnomalyOutput, GroundTruth]:
    nodes = (
        Node("frontend", NodeKind.SERVICE, "frontend"),
        Node("cart-service", NodeKind.SERVICE, "cart-service", runs_on="node-a"),
        Node("cart-db", NodeKind.INFRA, "cart-db"),  # treated as sync leaf / DB
        Node("payment-service", NodeKind.SERVICE, "payment-service"),
        Node("notifier", NodeKind.SERVICE, "notifier"),
        Node("node-a", NodeKind.INFRA, "node-a"),
    )

    edges = (
        Edge("frontend", "cart-service", CallType.SYNC, degraded=True),
        Edge("cart-service", "cart-db", CallType.SYNC, degraded=True),
        Edge("frontend", "payment-service", CallType.SYNC, degraded=True),
        Edge("payment-service", "notifier", CallType.ASYNC, degraded=False),
        Edge("node-a", "cart-service", CallType.RUNS_ON, degraded=False),
    )

    topology = Topology(nodes=nodes, edges=edges)

    # One anomalous request path hitting the DB.
    spans = (
        Span("s1", "frontend", None, 0.0, 500.0, "SERVER"),
        Span("s2", "cart-service", "s1", 10.0, 400.0, "CLIENT",
             attributes={"peer.service": "cart-db"}),
        Span("s3", "cart-db", "s2", 20.0, 350.0, "SERVER"),
        Span("s4", "payment-service", "s1", 15.0, 200.0, "CLIENT"),
    )
    traces = (
        Trace("trace-anomalous", spans, anomalous=True),
        Trace(
            "trace-ok",
            (
                Span("s5", "frontend", None, 0.0, 50.0, "SERVER"),
                Span("s6", "notifier", "s5", 5.0, 40.0, "CONSUMER",
                     attributes={"messaging.system": "kafka"}),
            ),
            anomalous=False,
        ),
    )

    anomaly = AnomalyOutput(
        anomaly_score={
            "cart-db": 0.95,
            "cart-service": 0.80,
            "frontend": 0.70,
            "payment-service": 0.55,
            "notifier": 0.25,
            "node-a": 0.20,
        },
        first_deviation_time={
            "cart-db": 0.0,
            "cart-service": 2.0,
            "frontend": 4.0,
            "payment-service": 5.0,
            "notifier": None,  # slow-burn
            "node-a": 10.0,
        },
        resource_deviation={
            "cart-db": True,
            "cart-service": True,
            "frontend": False,
            "payment-service": False,
            "notifier": False,
            "node-a": False,
        },
        anomalous_trace_ids=frozenset({"trace-anomalous"}),
        t0=0.0,
    )

    ground_truth = GroundTruth(
        expected_cluster_count=1,
        origin_nodes=frozenset({"cart-db"}),
    )
    return topology, traces, anomaly, ground_truth
