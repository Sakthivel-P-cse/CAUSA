"""Fixture S4 — Kafka shared-dependency confound.

Derived from testbed/chaos/S4_kafka_shared_dep.yaml: `causa/true-root: kafka`,
`causa/fault-class: Dependency`. NetworkChaos delays traffic to kafka; both
accounting and fraud degrade with no service edge between them — only the
shared async backbone explains both.

Topology (call direction; ASYNC edges kept producer → consumer in the IG):

  checkout ──async──► kafka ◄──async── accounting
                        ▲
                        └──async── fraud

  No edge between accounting and fraud.

Timeline — kafka moves first; victims light up later and LOOK worse:
  kafka       FirstDeviation=0.0   anomaly=0.55   ← TRUE ROOT
  checkout    FirstDeviation=5.0   anomaly=0.68
  accounting  FirstDeviation=6.0   anomaly=0.69
  fraud       FirstDeviation=7.0   anomaly=0.68

Victim anomalies sit above kafka but below the isolation strong-origin bar
(0.7). ASYNC edges keep producer→consumer, so every caller is a local source;
only the earliest (kafka) is retained for origin clustering.
"""

from __future__ import annotations

from contracts import CallType, NodeKind
from graphs.types import Edge, Node, Span, Topology, Trace
from rca.types import KernelAnomaly

from .types import FixtureExpectation


def load_incident_s4() -> tuple[
    Topology, tuple[Trace, ...], KernelAnomaly, FixtureExpectation
]:
    nodes = (
        Node("checkout", NodeKind.SERVICE, "checkout"),
        Node("accounting", NodeKind.SERVICE, "accounting"),
        Node("fraud", NodeKind.SERVICE, "fraud"),
        Node("kafka", NodeKind.DEPENDENCY, "kafka"),
    )

    edges = (
        Edge("checkout", "kafka", CallType.ASYNC, degraded=True),
        Edge("accounting", "kafka", CallType.ASYNC, degraded=True),
        Edge("fraud", "kafka", CallType.ASYNC, degraded=True),
    )

    topology = Topology(nodes=nodes, edges=edges)

    spans = (
        Span(
            "t1", "checkout", None, 0.0, 800.0, "PRODUCER",
            attributes={"messaging.system": "kafka"},
        ),
        Span(
            "t2", "accounting", None, 10.0, 900.0, "CONSUMER",
            attributes={"messaging.system": "kafka"},
        ),
        Span(
            "t3", "fraud", None, 15.0, 880.0, "CONSUMER",
            attributes={"messaging.system": "kafka"},
        ),
        Span("t4", "kafka", "t1", 20.0, 750.0, "SERVER"),
    )
    traces = (Trace("trace-s4", spans, anomalous=True),)

    anomaly = KernelAnomaly(
        anomaly_score={
            "accounting": 0.69,
            "fraud": 0.68,
            "checkout": 0.68,
            "kafka": 0.55,
        },
        first_deviation_time={
            "kafka": 0.0,
            "checkout": 5.0,
            "accounting": 6.0,
            "fraud": 7.0,
        },
        resource_deviation={
            "kafka": True,
            "checkout": False,
            "accounting": True,
            "fraud": True,
        },
        anomalous_trace_ids=frozenset({"trace-s4"}),
        t0=0.0,
    )

    ground_truth = FixtureExpectation(
        incident_id="s4-kafka-shared-dep",
        true_root_node="kafka",
        fault_class="Dependency",
        inject_start=0.0,
        inject_end=30.0,
        expected_cluster_count=1,
        origin_nodes=frozenset({"kafka"}),
    )
    return topology, traces, anomaly, ground_truth
