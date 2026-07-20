"""
Invariants on the ranked output that the fixtures did not pin.

Each test here corresponds to a defect found in review:
  - confidence was non-monotone with rank (last place ≈ #2 on S0)
  - derive_role labelled essentially-healthy nodes ORIGIN
  - arbitration's +1e-3 bump capped a correct infra promotion at ~0.001
  - fault_class was never assigned, so failure_type was always NULL
"""

from __future__ import annotations

import pytest

from contracts import CallType, NodeKind, Role, RootCauseCandidate, SignalScores
from graphs.influence_graph import InfluenceGraph
from graphs.types import Edge, Node
from rca.types import KernelAnomaly
from scoring.arbitration import arbitrate
from scoring.fault_class import derive_fault_class
from scoring.output import stamp_confidence
from scoring.signals import derive_role


def _cand(node_id: str, score: float, *, kind=NodeKind.SERVICE, role=Role.VICTIM):
    return RootCauseCandidate(
        node_id=node_id,
        node_kind=kind,
        score=score,
        confidence=0.0,
        role=role,
        signals=SignalScores(T=0.0, D=0.0, P=0.0),
        dev_vector={"anomaly": score},
        explanation=f"{node_id}. Role: {role.value}.",
    )


# ── confidence monotonicity ───────────────────────────────────────────


@pytest.mark.parametrize(
    "scores",
    [
        [1.0, 0.9, 0.1],          # big gap below #2 used to beat #1
        [1.0, 0.76, 0.5, 0.33, 0.23],   # the S0 shape
        [0.5, 0.5, 0.5],          # all tied
        [0.9],                    # sole candidate
    ],
)
def test_confidence_never_increases_with_rank(scores):
    candidates = [_cand(f"n{i}", s) for i, s in enumerate(scores)]
    stamp_confidence(candidates)
    confidences = [c.confidence for c in candidates]
    assert confidences == sorted(confidences, reverse=True), (
        f"confidence must not rise as score falls: {confidences}"
    )


def test_last_candidate_does_not_inherit_full_score_as_confidence():
    """The last row compares against a 0.0 sentinel — it must not read as
    'infinitely separated' and outrank mid-list candidates."""
    candidates = [_cand("a", 1.0), _cand("b", 0.76), _cand("c", 0.23)]
    stamp_confidence(candidates)
    assert candidates[-1].confidence < candidates[-1].score
    assert candidates[-1].confidence <= candidates[0].confidence


def test_sole_candidate_keeps_full_separation():
    candidates = [_cand("only", 0.8)]
    stamp_confidence(candidates)
    assert candidates[0].confidence == pytest.approx(0.8)


# ── role gating ───────────────────────────────────────────────────────


def test_outward_heavy_but_healthy_node_is_not_an_origin():
    """High D with negligible anomaly must not be labelled ORIGIN — that put
    healthy nodes (S0's node-a at 0.20) into persisted origin_candidates."""
    assert derive_role(D=0.9, anomaly_score=0.05, betweenness=0.0, in_influence=0.0) is Role.UNKNOWN


def test_outward_heavy_and_deviating_node_is_still_an_origin():
    assert derive_role(D=0.9, anomaly_score=0.30, betweenness=0.0, in_influence=0.0) is Role.ORIGIN
    assert derive_role(D=0.9, anomaly_score=0.80, betweenness=0.0, in_influence=0.0) is Role.ORIGIN


# ── arbitration promotes without destroying confidence ────────────────


def _infra_over_service_graph():
    ig = InfluenceGraph(
        nodes={
            "node-x": Node("node-x", NodeKind.INFRA, "node-x"),
            "svc": Node("svc", NodeKind.SERVICE, "svc", runs_on="node-x"),
            "downstream": Node("downstream", NodeKind.SERVICE, "downstream"),
        },
        edges=[
            Edge("node-x", "svc", CallType.RUNS_ON, degraded=True),
            Edge("svc", "downstream", CallType.SYNC, degraded=True),
        ],
    )
    anomaly = KernelAnomaly(
        anomaly_score={"node-x": 0.9, "svc": 0.7, "downstream": 0.3},
        first_deviation_time={"node-x": 10.0, "svc": 25.0, "downstream": 40.0},
        resource_deviation={"node-x": True, "svc": False, "downstream": False},
    )
    return ig, anomaly


def test_arbitration_promotes_infra_above_hosted_service():
    ig, anomaly = _infra_over_service_graph()
    candidates = [
        _cand("svc", 0.80, role=Role.ORIGIN),
        _cand("node-x", 0.40, kind=NodeKind.INFRA),
        _cand("downstream", 0.10),
    ]
    ranked = arbitrate(candidates, ig, anomaly)

    assert ranked[0].node_id == "node-x", "infra leading its host must be promoted"
    by_id = {c.node_id: c for c in ranked}
    assert by_id["svc"].role is Role.VICTIM_OF_INFRA
    assert "victim-of-infra" in by_id["svc"].explanation


def test_arbitration_promotion_preserves_a_usable_confidence():
    """The old +1e-3 bump made the top-2 margin exactly 0.001, so a CORRECT
    infra attribution was persisted with ~0.001 confidence."""
    ig, anomaly = _infra_over_service_graph()
    candidates = [
        _cand("svc", 0.80, role=Role.ORIGIN),
        _cand("node-x", 0.40, kind=NodeKind.INFRA),
        _cand("downstream", 0.10),
    ]
    ranked = arbitrate(candidates, ig, anomaly)
    stamp_confidence(ranked)

    assert ranked[0].confidence > 0.01, (
        f"promoted infra confidence collapsed to {ranked[0].confidence}"
    )
    # The score distribution is preserved by the swap, so the margin below the
    # new top is the one the scorer actually produced.
    assert ranked[0].score == pytest.approx(0.80)
    assert ranked[1].score == pytest.approx(0.40)


def test_arbitration_is_a_noop_without_a_runs_on_edge():
    ig, anomaly = _infra_over_service_graph()
    ig.edges = [e for e in ig.edges if e.call_type != CallType.RUNS_ON]
    candidates = [_cand("svc", 0.80), _cand("node-x", 0.40, kind=NodeKind.INFRA)]
    ranked = arbitrate(candidates, ig, anomaly)
    assert [c.node_id for c in ranked] == ["svc", "node-x"]
    assert all(c.role is not Role.VICTIM_OF_INFRA for c in ranked)


# ── fault_class ───────────────────────────────────────────────────────


def test_hosting_node_at_top_yields_infra():
    ig, anomaly = _infra_over_service_graph()
    candidates = [_cand("node-x", 0.9, kind=NodeKind.INFRA), _cand("svc", 0.4)]
    assert derive_fault_class(candidates, ig) == "Infra"


def test_database_typed_as_infra_is_not_called_an_infra_fault():
    """Fixtures type a DB as NodeKind.INFRA, so node_kind alone would misfire.
    A DB sources no runs-on edge, so it must not be classed Infra."""
    ig = InfluenceGraph(
        nodes={
            "cart-db": Node("cart-db", NodeKind.INFRA, "cart-db"),
            "cart": Node("cart", NodeKind.SERVICE, "cart"),
        },
        edges=[Edge("cart-db", "cart", CallType.SYNC, degraded=True)],
    )
    candidates = [_cand("cart-db", 0.9, kind=NodeKind.INFRA), _cand("cart", 0.4)]
    assert derive_fault_class(candidates, ig) is None


def test_resource_dominant_metric_yields_resource():
    ig = InfluenceGraph(
        nodes={"svc": Node("svc", NodeKind.SERVICE, "svc")}, edges=[]
    )
    top = _cand("svc", 0.9)
    top.dev_vector = {"cpu": 0.91, "error_rate": 0.10}
    assert derive_fault_class([top], ig) == "Resource"


def test_red_dominant_metric_is_left_unclassified():
    ig = InfluenceGraph(
        nodes={"svc": Node("svc", NodeKind.SERVICE, "svc")}, edges=[]
    )
    top = _cand("svc", 0.9)
    top.dev_vector = {"cpu": 0.10, "error_rate": 0.91}
    assert derive_fault_class([top], ig) is None, (
        "AppLogic/Config/Dependency/Data need evidence the kernel lacks — "
        "None is honest, a guess is not"
    )


def test_no_candidates_yields_no_class():
    ig = InfluenceGraph(nodes={}, edges=[])
    assert derive_fault_class([], ig) is None
