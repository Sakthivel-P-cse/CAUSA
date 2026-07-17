"""Second fixture S1 — infra-caused failure (node pressure → service victim).

Topology:

  api ──sync──► catalog ──sync──► catalog-db
   │                │
   │                └──runs-on──► node-b   (infra origin: early, high anomaly)
   └──sync──► search

Timeline:
  node-b      FirstDeviation=0.0   anomaly=0.92   ← infra origin
  catalog     FirstDeviation=3.0   anomaly=0.75   ← victim-of-infra after arb
  catalog-db  FirstDeviation=5.0   anomaly=0.40
  api         FirstDeviation=6.0   anomaly=0.60
  search      FirstDeviation=7.0   anomaly=0.35
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


def load_incident_s1() -> tuple[Topology, tuple[Trace, ...], AnomalyOutput, GroundTruth]:
    nodes = (
        Node("api", NodeKind.SERVICE, "api"),
        Node("catalog", NodeKind.SERVICE, "catalog", runs_on="node-b"),
        Node("catalog-db", NodeKind.INFRA, "catalog-db"),
        Node("search", NodeKind.SERVICE, "search"),
        Node("node-b", NodeKind.INFRA, "node-b"),
    )

    edges = (
        Edge("api", "catalog", CallType.SYNC, degraded=True),
        Edge("catalog", "catalog-db", CallType.SYNC, degraded=True),
        Edge("api", "search", CallType.SYNC, degraded=False),
        Edge("node-b", "catalog", CallType.RUNS_ON, degraded=True),
    )

    topology = Topology(nodes=nodes, edges=edges)

    spans = (
        Span("t1", "api", None, 0.0, 400.0, "SERVER"),
        Span("t2", "catalog", "t1", 10.0, 350.0, "CLIENT"),
        Span("t3", "catalog-db", "t2", 20.0, 300.0, "SERVER"),
        Span("t4", "search", "t1", 15.0, 100.0, "CLIENT"),
    )
    traces = (Trace("trace-s1", spans, anomalous=True),)

    anomaly = AnomalyOutput(
        anomaly_score={
            "node-b": 0.92,
            "catalog": 0.75,
            "api": 0.60,
            "catalog-db": 0.40,
            "search": 0.35,
        },
        first_deviation_time={
            "node-b": 0.0,
            "catalog": 3.0,
            "catalog-db": 5.0,
            "api": 6.0,
            "search": 7.0,
        },
        resource_deviation={
            "node-b": True,
            "catalog": True,
            "api": False,
            "catalog-db": False,
            "search": False,
        },
        anomalous_trace_ids=frozenset({"trace-s1"}),
        t0=0.0,
    )

    ground_truth = GroundTruth(
        expected_cluster_count=1,
        origin_nodes=frozenset({"node-b"}),
    )
    return topology, traces, anomaly, ground_truth
