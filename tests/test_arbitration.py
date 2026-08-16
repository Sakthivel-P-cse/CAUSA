"""Arbitration + explanation validation tests."""

from contracts.types import Role
from fixtures.incident_s0 import load_incident_s0
from fixtures.incident_s1 import load_incident_s1
from graphs.incident_subgraph import build_incident_subgraph
from graphs.influence_graph import build_influence_graph
from scoring.output import validate_explanation
from scoring.scorer import score


def test_every_candidate_has_valid_explanation_s0():
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = score(sub)
    assert result.candidates
    for c in result.candidates:
        assert c.explanation.strip()
        validate_explanation(c)


def test_arbitration_victim_of_infra_on_s1():
    topology, traces, anomaly, _ = load_incident_s1()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = score(sub, top_c=5)

    by_id = {c.node_id: c for c in result.candidates}
    assert "node-b" in by_id
    assert "catalog" in by_id
    assert by_id["catalog"].role == Role.VICTIM_OF_INFRA

    # Infra promoted above the hosted service.
    idx = {c.node_id: i for i, c in enumerate(result.candidates)}
    assert idx["node-b"] < idx["catalog"]


def test_s0_cart_db_ranks_near_top():
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = score(sub)
    top = result.candidates[0]
    # cart-db should be top or near-top origin.
    top_ids = [c.node_id for c in result.candidates[:2]]
    assert "cart-db" in top_ids
    assert any(
        c.node_id == "cart-db" and c.role == Role.ORIGIN for c in result.candidates
    )
