"""Kernel pipeline tests on Dev B's synthetic S0, S1, and S5 fixtures."""

from contracts import Role
from fixtures.incident_s0 import load_incident_s0
from fixtures.incident_s1 import load_incident_s1
from fixtures.incident_s5 import load_incident_s5
from graphs.construction import (
    build_infrastructure_graph,
    build_service_dependency_graph,
    build_trace_execution_graph,
)
from graphs.incident_subgraph import build_incident_subgraph
from graphs.influence_graph import build_influence_graph
from scoring.scorer import score


def test_four_graphs_build_on_s0():
    topology, traces, anomaly, _ = load_incident_s0()
    infra = build_infrastructure_graph(topology)
    svc = build_service_dependency_graph(topology)
    trace_g = build_trace_execution_graph(traces)
    sub = build_incident_subgraph(topology, traces, anomaly)

    assert "node-a" in infra.nodes or any(
        e.call_type.value == "runs-on" for e in infra.edges
    )
    assert "cart-service" in svc.nodes or "cart-db" in svc.nodes
    assert len(trace_g.nodes) >= 4
    assert "cart-db" in sub.nodes
    assert "notifier" in sub.nodes  # slow-burn kept via θ_low connectivity


def test_e2e_s0_origin_db():
    topology, traces, anomaly, gt = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    ig = build_influence_graph(sub)
    # Gate: DB has outgoing influence
    assert ig.out_degree("cart-db") > 0

    result = score(sub, ig=ig)
    top2 = [c.node_id for c in result.candidates[:2]]
    assert "cart-db" in top2
    db = next(c for c in result.candidates if c.node_id == "cart-db")
    assert db.role == Role.ORIGIN
    assert db.T == 0.5 or db.T > 0.5  # earliest → high T (not slow-burn)
    assert db.T > 0.9
    assert db.confidence >= 0.0
    assert db.node_kind.value in {"infra", "dependency", "service"}


def test_e2e_s1_infra_origin():
    topology, traces, anomaly, gt = load_incident_s1()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = score(sub)
    by_id = {c.node_id: c for c in result.candidates}

    assert by_id["catalog"].role == Role.VICTIM_OF_INFRA
    idx = {c.node_id: i for i, c in enumerate(result.candidates)}
    assert idx["node-b"] < idx["catalog"]
    # Top candidate should be the infra origin (or near top).
    top3 = [c.node_id for c in result.candidates[:3]]
    assert "node-b" in top3


def test_e2e_s5_victim_of_infra():
    """DoD S5: infra above service; hosted service role=victim-of-infra."""
    topology, traces, anomaly, gt = load_incident_s5()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = score(sub, incident_id=gt.incident_id)
    by_id = {c.node_id: c for c in result.candidates}

    assert result.incident_id == gt.incident_id
    assert by_id["catalog"].role == Role.VICTIM_OF_INFRA
    assert by_id["node-b"].node_kind.value == "infra"
    assert by_id["catalog"].node_kind.value == "service"
    idx = {c.node_id: i for i, c in enumerate(result.candidates)}
    assert idx["node-b"] < idx["catalog"]
    for c in result.candidates:
        assert c.confidence >= 0.0
        assert hasattr(c.signals, "T")
