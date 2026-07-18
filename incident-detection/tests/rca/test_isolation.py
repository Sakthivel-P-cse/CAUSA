"""Multi-fault isolation + DoD fixtures D1/D2/D3."""

from fixtures.incident_d1 import load_incident_d1
from fixtures.incident_d2 import load_incident_d2
from fixtures.incident_d3 import load_incident_d3
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


def test_isolation_d1_disjoint_cluster_count():
    topology, traces, anomaly, gt = load_incident_d1()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = isolate(sub)
    assert result.abstain_reason is None
    assert result.cluster_count == gt.expected_cluster_count == 2


def test_isolation_d2_shared_dep_cluster_count():
    """Stage 3 must split two independent origins sharing a downstream dep."""
    topology, traces, anomaly, gt = load_incident_d2()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = isolate(sub)
    assert result.abstain_reason is None
    assert result.cluster_count == gt.expected_cluster_count == 2


def test_isolation_d3_abstains_when_causally_adjacent():
    topology, traces, anomaly, gt = load_incident_d3()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = isolate(sub, delta=5.0)
    assert gt.ambiguous
    assert result.abstain_reason == "MULTI-INCIDENT — ambiguous"
