"""Multi-fault isolation tests."""

from contracts.types import (
    AnomalyOutput,
    CallType,
    Edge,
    GroundTruth,
    Node,
    NodeKind,
    Topology,
    Trace,
)
from fixtures.incident_s0 import load_incident_s0
from graphs.incident_subgraph import build_incident_subgraph
from isolation.pipeline import isolate


def test_isolation_single_cluster_on_s0():
    topology, traces, anomaly, gt = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = isolate(sub)
    assert result.abstain_reason is None
    assert result.cluster_count >= 1
    # S0 is one fault — expect a single cluster after gap-repair.
    assert result.cluster_count == gt.expected_cluster_count


def _two_fault_fixture():
    """Two disconnected fault regions."""
    nodes = (
        Node("a-svc", NodeKind.SERVICE, "a-svc"),
        Node("a-db", NodeKind.INFRA, "a-db"),
        Node("b-svc", NodeKind.SERVICE, "b-svc"),
        Node("b-db", NodeKind.INFRA, "b-db"),
    )
    edges = (
        Edge("a-svc", "a-db", CallType.SYNC, degraded=True),
        Edge("b-svc", "b-db", CallType.SYNC, degraded=True),
    )
    topology = Topology(nodes=nodes, edges=edges)
    traces = ()
    anomaly = AnomalyOutput(
        anomaly_score={"a-db": 0.9, "a-svc": 0.7, "b-db": 0.88, "b-svc": 0.65},
        first_deviation_time={"a-db": 0.0, "a-svc": 2.0, "b-db": 0.5, "b-svc": 2.5},
        resource_deviation={"a-db": True, "a-svc": False, "b-db": True, "b-svc": False},
    )
    gt = GroundTruth(expected_cluster_count=2, origin_nodes=frozenset({"a-db", "b-db"}))
    return topology, traces, anomaly, gt


def test_isolation_two_disconnected_faults():
    topology, traces, anomaly, gt = _two_fault_fixture()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = isolate(sub)
    assert result.abstain_reason is None
    assert result.cluster_count == 2


def test_isolation_abstains_when_causally_adjacent():
    """Two early origins that can reach each other within Δ → abstain."""
    nodes = (
        Node("db1", NodeKind.INFRA, "db1"),
        Node("svc", NodeKind.SERVICE, "svc"),
        Node("db2", NodeKind.INFRA, "db2"),
    )
    # Both DBs called by same service — after IG reverse, both DBs → svc.
    edges = (
        Edge("svc", "db1", CallType.SYNC, degraded=True),
        Edge("svc", "db2", CallType.SYNC, degraded=True),
    )
    topology = Topology(nodes=nodes, edges=edges)
    anomaly = AnomalyOutput(
        anomaly_score={"db1": 0.9, "db2": 0.9, "svc": 0.8},
        first_deviation_time={"db1": 0.0, "db2": 0.5, "svc": 2.0},
        resource_deviation={"db1": True, "db2": True, "svc": True},
    )
    sub = build_incident_subgraph(topology, (), anomaly)
    result = isolate(sub, delta=5.0)
    # Shared victim + near-simultaneous origins → ambiguous.
    assert result.abstain_reason == "MULTI-INCIDENT — ambiguous"
