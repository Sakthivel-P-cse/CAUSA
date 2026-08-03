"""
ORIGIN is an incident-level claim, resolved after ranking.

`derive_role` decides ORIGIN from one node's own signals, which cannot express a
comparative claim. D is a ratio of outward to inward influence, so ANY
zero-in-degree node scores D=1.0 by construction and clears the threshold — S5
injects one fault and reported three origins that way. `resolve_origins` keeps
the label on the top-ranked candidate only, per cluster.
"""

from __future__ import annotations

import pytest

from contracts import NodeKind, Role, RootCauseCandidate, SignalScores
from fixtures.incident_d1 import load_incident_d1
from fixtures.incident_s0 import load_incident_s0
from fixtures.incident_s5 import load_incident_s5
from graphs.incident_subgraph import build_incident_subgraph
from graphs.influence_graph import build_influence_graph
from isolation.pipeline import isolate
from scoring.output import resolve_origins
from scoring.scorer import score


def _cand(node_id, sc, role, *, P=0.0):
    return RootCauseCandidate(
        node_id=node_id,
        node_kind=NodeKind.SERVICE,
        score=sc,
        confidence=0.0,
        role=role,
        signals=SignalScores(T=0.0, D=0.0, P=P),
        dev_vector={},
        explanation=f"{node_id}. Role: {role.value}.",
    )


# ── unit behavior ─────────────────────────────────────────────────────


def test_only_top_ranked_keeps_origin():
    candidates = [
        _cand("root", 1.0, Role.ORIGIN),
        _cand("side", 0.4, Role.ORIGIN),
        _cand("other", 0.3, Role.ORIGIN),
    ]
    resolve_origins(candidates)
    assert [c.role for c in candidates] == [Role.ORIGIN, Role.UNKNOWN, Role.UNKNOWN]


def test_demoted_origin_that_propagates_becomes_amplifier():
    """P > 0 means outgoing influence is temporally consistent AND degraded —
    that is what an amplifier does."""
    candidates = [
        _cand("root", 1.0, Role.ORIGIN),
        _cand("relay", 0.5, Role.ORIGIN, P=0.8),
    ]
    resolve_origins(candidates)
    assert candidates[1].role is Role.AMPLIFIER


def test_demoted_origin_that_propagates_nothing_becomes_unknown():
    """Deviated, causes nothing downstream, explained by nothing upstream —
    genuinely uncharacterized. Saying so beats inventing a role."""
    candidates = [
        _cand("root", 1.0, Role.ORIGIN),
        _cand("island", 0.5, Role.ORIGIN, P=0.0),
    ]
    resolve_origins(candidates)
    assert candidates[1].role is Role.UNKNOWN


def test_non_origin_roles_are_untouched():
    candidates = [
        _cand("root", 1.0, Role.ORIGIN),
        _cand("amp", 0.6, Role.AMPLIFIER),
        _cand("vic", 0.4, Role.VICTIM),
        _cand("infra_vic", 0.2, Role.VICTIM_OF_INFRA),
    ]
    resolve_origins(candidates)
    assert [c.role for c in candidates[1:]] == [
        Role.AMPLIFIER, Role.VICTIM, Role.VICTIM_OF_INFRA
    ]


def test_victim_of_infra_survives_because_it_is_a_different_axis():
    """Arbitration's cross-layer verdict must not be overwritten — a service can
    be both 'not the origin' and 'victim of its host'."""
    candidates = [
        _cand("host", 1.0, Role.ORIGIN),
        _cand("hosted", 0.6, Role.VICTIM_OF_INFRA),
    ]
    resolve_origins(candidates)
    assert candidates[1].role is Role.VICTIM_OF_INFRA


def test_demotion_syncs_the_explanation_text():
    candidates = [_cand("root", 1.0, Role.ORIGIN), _cand("side", 0.4, Role.ORIGIN)]
    resolve_origins(candidates)
    assert "Role: unknown." in candidates[1].explanation
    assert "Role: origin." not in candidates[1].explanation
    assert candidates[1].flags["demoted_secondary_origin"] is True


def test_top_candidate_is_not_promoted_to_origin():
    """If the evidence does not support ORIGIN, do not assert it."""
    candidates = [_cand("weak", 0.3, Role.VICTIM), _cand("other", 0.2, Role.ORIGIN)]
    resolve_origins(candidates)
    assert candidates[0].role is Role.VICTIM
    assert candidates[1].role is Role.UNKNOWN


def test_empty_list_is_safe():
    resolve_origins([])


# ── end-to-end on fixtures ────────────────────────────────────────────


@pytest.mark.parametrize(
    "loader,expected",
    [(load_incident_s0, ["cart-db"]), (load_incident_s5, ["node-b"])],
)
def test_single_fault_scenarios_report_exactly_one_origin(loader, expected):
    topology, traces, anomaly, _ = loader()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = score(sub, ig=build_influence_graph(sub), anomaly=anomaly)
    origins = [c.node_id for c in result.candidates if c.role is Role.ORIGIN]
    assert origins == expected


def test_s5_no_longer_reports_three_origins_for_one_fault():
    """The concrete regression: catalog-db (anomaly 0.40, deviated 5s late) and
    search (0.35, 7s late) were published as causes alongside the real root
    purely because both are zero-in-degree IG sources."""
    topology, traces, anomaly, _ = load_incident_s5()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = score(sub, ig=build_influence_graph(sub), anomaly=anomaly)
    by_id = {c.node_id: c for c in result.candidates}
    assert by_id["catalog-db"].role is not Role.ORIGIN
    assert by_id["search"].role is not Role.ORIGIN
    assert by_id["node-b"].role is Role.ORIGIN


def test_multi_fault_incident_keeps_one_origin_per_cluster():
    """Demotion is per cluster, so a genuine dual incident does not collapse to
    a single global winner."""
    topology, traces, anomaly, gt = load_incident_d1()
    sub = build_incident_subgraph(topology, traces, anomaly)
    result = isolate(sub, anomaly=anomaly)
    assert len(result.ranked_by_cluster) == 2

    origins = []
    for ranked in result.ranked_by_cluster.values():
        cluster_origins = [
            c.node_id for c in ranked.candidates if c.role is Role.ORIGIN
        ]
        assert len(cluster_origins) == 1, "exactly one origin per cluster"
        origins += cluster_origins
    assert sorted(origins) == sorted(gt.origin_nodes)
