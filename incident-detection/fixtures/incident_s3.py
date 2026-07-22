"""Fixture S3 — clock skew / Timing (ordering robustness).

Derived from testbed/chaos/S3_clock_skew.yaml: `causa/true-root: checkout`,
`causa/fault-class: Timing`. StressChaos + TimeChaos both hit checkout; the
skew corrupts wall-clock while a real fault makes checkout deviate first.

Topology (call direction):

  frontend ──sync──► checkout ──sync──► payment
                         │
                         └──runs-on──► node-c   (optional host; healthy)

Timeline — causal FirstDeviation keeps checkout as origin even when its
wall-clock span starts earlier than its parent (TimeChaos offset):
  checkout   FirstDeviation=0.0   anomaly=0.72   ← TRUE ROOT
  payment    FirstDeviation=4.0   anomaly=0.68
  frontend   FirstDeviation=7.0   anomaly=0.85
  node-c     FirstDeviation=None  anomaly=0.10

Checkout ranks first via temporal primacy + sync-edge orientation. Payment
anomaly stays below the isolation strong-origin bar (0.7) so the sync-leaf
does not split the component after edge reversal.
"""

from __future__ import annotations

from contracts import CallType, NodeKind
from graphs.types import Edge, Node, Span, Topology, Trace
from rca.types import KernelAnomaly

from .types import FixtureExpectation


def load_incident_s3() -> tuple[
    Topology, tuple[Trace, ...], KernelAnomaly, FixtureExpectation
]:
    nodes = (
        Node("frontend", NodeKind.SERVICE, "frontend"),
        Node("checkout", NodeKind.SERVICE, "checkout", runs_on="node-c"),
        Node("payment", NodeKind.SERVICE, "payment"),
        Node("node-c", NodeKind.INFRA, "node-c"),
    )

    edges = (
        Edge("frontend", "checkout", CallType.SYNC, degraded=True),
        Edge("checkout", "payment", CallType.SYNC, degraded=True),
        Edge("node-c", "checkout", CallType.RUNS_ON, degraded=False),
    )

    topology = Topology(nodes=nodes, edges=edges)

    # Checkout wall-clock is skewed 500ms earlier than its parent (TimeChaos
    # timeOffset: "-500ms") while parent/child linkage still places it under
    # frontend — ordering must follow the DAG, not the skewed clock.
    spans = (
        Span("t1", "frontend", None, 0.0, 900.0, "SERVER"),
        Span("t2", "checkout", "t1", -500.0, 700.0, "CLIENT"),
        Span("t3", "payment", "t2", 40.0, 650.0, "SERVER"),
    )
    traces = (Trace("trace-s3", spans, anomalous=True),)

    anomaly = KernelAnomaly(
        anomaly_score={
            "frontend": 0.85,
            "checkout": 0.72,
            "payment": 0.68,
            "node-c": 0.10,
        },
        first_deviation_time={
            "checkout": 0.0,
            "payment": 4.0,
            "frontend": 7.0,
            "node-c": None,
        },
        resource_deviation={
            "checkout": True,
            "payment": False,
            "frontend": False,
            "node-c": False,
        },
        anomalous_trace_ids=frozenset({"trace-s3"}),
        t0=0.0,
    )

    ground_truth = FixtureExpectation(
        incident_id="s3-clock-skew",
        true_root_node="checkout",
        fault_class="Timing",
        inject_start=0.0,
        inject_end=30.0,
        expected_cluster_count=1,
        origin_nodes=frozenset({"checkout"}),
    )
    return topology, traces, anomaly, ground_truth
