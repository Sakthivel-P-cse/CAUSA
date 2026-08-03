"""
Temporal normalization basis, cross-cluster commensurability, and cycles.

Three defects that all corrupt ranking without failing loudly:
  - t0 was a min over the PERMISSIVE subgraph, so one merely-adjacent node that
    deviated early rescaled T for every node (measured: 5 of 6 on S0).
  - each cluster derived its horizon from its own spread, then the orchestrator
    merged all clusters and sorted by raw score as if commensurable.
  - a mutual call is a 2-cycle after sync reversal, where D is symmetric by
    construction and labelled BOTH ends origin.
"""

from __future__ import annotations

import pytest

from contracts import CallType, NodeKind
from fixtures.incident_s0 import load_incident_s0
from graphs.incident_subgraph import IncidentSubgraph, build_incident_subgraph
from graphs.influence_graph import InfluenceGraph, build_influence_graph
from graphs.types import Edge, Node
from isolation.pipeline import _shared_horizon, isolate
from rca.types import KernelAnomaly
from scoring.signals import compute_signals


def _reanchor(anomaly: KernelAnomaly, node: str, fdt: float) -> KernelAnomaly:
    return KernelAnomaly(
        anomaly_score=dict(anomaly.anomaly_score),
        first_deviation_time={**anomaly.first_deviation_time, node: fdt},
        resource_deviation=dict(anomaly.resource_deviation),
        dev_vector=dict(anomaly.dev_vector),
    )


# ── normalization basis ───────────────────────────────────────────────


def test_subgraph_separates_seeds_from_expanded_neighbours():
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    assert sub.seed_ids, "direct-evidence nodes must be recorded"
    assert sub.seed_ids < set(sub.nodes), (
        "connectivity expansion must admit nodes beyond the seeds — otherwise "
        "there is nothing for the seed basis to protect against"
    )


def test_early_non_seed_neighbour_does_not_rescale_other_nodes():
    """The blocking bug: t0 is a min, so an adjacent node that deviated earlier
    shifted the whole timescale and rewrote every node's T."""
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    ig = build_influence_graph(sub)

    victim = sorted(set(sub.nodes) - sub.seed_ids)[0]
    before = compute_signals(ig, anomaly, seed_ids=sub.seed_ids)
    after = compute_signals(
        ig, _reanchor(anomaly, victim, -30.0), seed_ids=sub.seed_ids
    )

    assert before.t0 == after.t0
    assert before.horizon == after.horizon
    for nid in before.by_node:
        if nid == victim:
            continue  # its own T legitimately moves with its own deviation time
        assert abs(before.by_node[nid].T - after.by_node[nid].T) < 1e-9, (
            f"{nid}'s T moved because a non-seed neighbour deviated early"
        )


def test_early_seed_still_moves_the_timescale():
    """Direct evidence must still count — the fix narrows the basis, it does
    not freeze it."""
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    ig = build_influence_graph(sub)

    seed = sorted(sub.seed_ids)[0]
    before = compute_signals(ig, anomaly, seed_ids=sub.seed_ids)
    after = compute_signals(
        ig, _reanchor(anomaly, seed, -30.0), seed_ids=sub.seed_ids
    )
    assert after.t0 < before.t0


def test_empty_seed_basis_falls_back_to_all_nodes():
    """An all-slow-burn cluster has no seeds; it must keep a timescale."""
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    ig = build_influence_graph(sub)
    narrowed = compute_signals(ig, anomaly, seed_ids=set())
    full = compute_signals(ig, anomaly)
    assert narrowed.t0 == full.t0
    assert narrowed.horizon == full.horizon


# ── cross-cluster commensurability ────────────────────────────────────


def _two_speed_clusters():
    """Disjoint clusters: one 2s cascade, one 40s cascade."""
    nodes = {
        n: Node(n, NodeKind.SERVICE, n)
        for n in ("a-db", "a-svc", "b-db", "b-svc")
    }
    edges = [
        Edge("a-svc", "a-db", CallType.SYNC, degraded=True),
        Edge("b-svc", "b-db", CallType.SYNC, degraded=True),
    ]
    anomaly = KernelAnomaly(
        anomaly_score={"a-db": 0.9, "a-svc": 0.8, "b-db": 0.9, "b-svc": 0.8},
        first_deviation_time={
            "a-db": 0.0, "a-svc": 2.0,        # tight
            "b-db": 100.0, "b-svc": 140.0,    # slow, and far away in time
        },
        resource_deviation={},
    )
    sub = IncidentSubgraph(
        nodes=nodes, edges=edges, anomaly=anomaly, seed_ids=set(nodes)
    )
    return sub, anomaly


def test_shared_horizon_uses_widest_cluster_not_the_gap_between_them():
    sub, anomaly = _two_speed_clusters()
    h = _shared_horizon(sub, anomaly, [{"a-db", "a-svc"}, {"b-db", "b-svc"}])
    assert h == 40.0, (
        "horizon must be the widest WITHIN-cluster spread; the 140s gap "
        "between independent incidents is not a propagation timescale"
    )


def test_cluster_origins_are_comparable_after_isolation():
    sub, anomaly = _two_speed_clusters()
    result = isolate(sub, anomaly=anomaly)
    assert len(result.ranked_by_cluster) == 2

    tops = [r.candidates[0] for r in result.ranked_by_cluster.values()]
    assert {c.node_id for c in tops} == {"a-db", "b-db"}
    # Each cluster's origin is first within its own incident, so both sit at the
    # top of the shared scale — neither is penalized for its incident starting
    # later or unfolding faster.
    assert all(abs(c.signals.T - 1.0) < 1e-9 for c in tops)


def test_long_spread_discriminates_mid_and_late_T():
    """Observed multi-minute spreads must not be capped by H_DEFAULT.

    Under the old ``min(max_delta, 60)`` rule, a node at +61s and one at +315s
    both collapsed to T≈0. With uncapped horizon, the mid node keeps positive T.
    """
    ig = InfluenceGraph(
        nodes={n: Node(n, NodeKind.SERVICE, n) for n in ("early", "mid", "late")},
        edges=[
            Edge("early", "mid", CallType.SYNC, degraded=True),
            Edge("mid", "late", CallType.SYNC, degraded=True),
        ],
    )
    anomaly = KernelAnomaly(
        anomaly_score={"early": 0.95, "mid": 0.8, "late": 0.7},
        first_deviation_time={"early": 0.0, "mid": 61.0, "late": 315.0},
        resource_deviation={},
    )
    signals = compute_signals(ig, anomaly)
    assert signals.horizon == pytest.approx(315.0)
    assert signals.by_node["early"].T == pytest.approx(1.0)
    assert signals.by_node["mid"].T > 0.5
    assert signals.by_node["late"].T == pytest.approx(0.0, abs=1e-9)
    assert signals.by_node["mid"].T > signals.by_node["late"].T


# ── cycles ────────────────────────────────────────────────────────────


def _mutual_call_graph():
    ig = InfluenceGraph(
        nodes={n: Node(n, NodeKind.SERVICE, n) for n in ("a", "b", "victim")},
        edges=[
            Edge("a", "b", CallType.SYNC, degraded=True),
            Edge("b", "a", CallType.SYNC, degraded=True),   # 2-cycle
            Edge("a", "victim", CallType.SYNC, degraded=True),
        ],
    )
    anomaly = KernelAnomaly(
        anomaly_score={"a": 0.9, "b": 0.85, "victim": 0.4},
        first_deviation_time={"a": 10.0, "b": 12.0, "victim": 30.0},
        resource_deviation={},
    )
    return ig, anomaly


def test_mutual_call_yields_exactly_one_origin():
    ig, anomaly = _mutual_call_graph()
    signals = compute_signals(ig, anomaly)
    origins = sorted(
        n for n, s in signals.by_node.items() if s.role.value == "origin"
    )
    assert origins == ["a"], (
        f"both ends of a 2-cycle were labelled origin: {origins}"
    )


def test_temporally_preceded_node_is_flagged():
    ig, anomaly = _mutual_call_graph()
    signals = compute_signals(ig, anomaly)
    assert signals.by_node["b"].flags.get("temporally_preceded") is True
    assert not signals.by_node["a"].flags.get("temporally_preceded")


def test_cycle_terminates():
    """A 3-cycle must not hang or recurse away."""
    ig = InfluenceGraph(
        nodes={n: Node(n, NodeKind.SERVICE, n) for n in ("x", "y", "z")},
        edges=[
            Edge("x", "y", CallType.SYNC, degraded=True),
            Edge("y", "z", CallType.SYNC, degraded=True),
            Edge("z", "x", CallType.SYNC, degraded=True),
        ],
    )
    anomaly = KernelAnomaly(
        anomaly_score={"x": 0.9, "y": 0.8, "z": 0.7},
        first_deviation_time={"x": 1.0, "y": 2.0, "z": 3.0},
        resource_deviation={},
    )
    signals = compute_signals(ig, anomaly)
    assert len(signals.by_node) == 3


def test_precedence_requires_the_predecessor_to_have_fired():
    """A quiet predecessor that happens to have an earlier timestamp is not
    evidence of an upstream fault."""
    ig = InfluenceGraph(
        nodes={n: Node(n, NodeKind.SERVICE, n) for n in ("quiet", "origin")},
        edges=[Edge("quiet", "origin", CallType.SYNC, degraded=False)],
    )
    anomaly = KernelAnomaly(
        anomaly_score={"quiet": 0.05, "origin": 0.9},   # quiet is below θ_fire
        first_deviation_time={"quiet": 1.0, "origin": 5.0},
        resource_deviation={},
    )
    signals = compute_signals(ig, anomaly)
    assert not signals.by_node["origin"].flags.get("temporally_preceded")


# ── D's zero-in-degree degeneracy ─────────────────────────────────────


def _source_vs_root():
    """A true root and an unreached bystander, both with zero in-degree."""
    ig = InfluenceGraph(
        nodes={
            n: Node(n, NodeKind.SERVICE, n)
            for n in ("root", "victim", "bystander")
        },
        edges=[
            Edge("root", "victim", CallType.SYNC, degraded=True),
            Edge("bystander", "victim", CallType.SYNC, degraded=False),
        ],
    )
    anomaly = KernelAnomaly(
        anomaly_score={"root": 0.90, "victim": 0.80, "bystander": 0.05},
        first_deviation_time={"root": 0.0, "victim": 20.0, "bystander": 40.0},
        resource_deviation={},
    )
    return ig, anomaly


def test_unreached_bystander_does_not_score_maximal_direction():
    """Zero in-degree means NO contrastive evidence, not maximal evidence.

    Unsmoothed, the ratio divides by its own numerator and returns exactly 1.0
    for any node nothing points at — identical to a genuine origin.
    """
    ig, anomaly = _source_vs_root()
    from scoring.signals import compute_D

    d_bystander, _, _ = compute_D("bystander", ig, anomaly)
    d_root, _, _ = compute_D("root", ig, anomaly)

    assert d_bystander < 1.0, "absence of inbound influence is not evidence"
    assert d_root > d_bystander, (
        "a root with strongly anomalous downstream must beat an idle source"
    )


def test_unsmoothed_d_ties_them_at_exactly_one():
    """Documents the defect the prior fixes: without smoothing the two are
    indistinguishable, both pinned at the ceiling."""
    ig, anomaly = _source_vs_root()
    from scoring.signals import compute_D

    # Not exactly 1.0 only because EPS guards the denominator.
    assert compute_D("bystander", ig, anomaly, prior=0.0)[0] == pytest.approx(1.0)
    assert compute_D("root", ig, anomaly, prior=0.0)[0] == pytest.approx(1.0)


def test_prior_is_swamped_by_strong_evidence():
    """Smoothing must not blunt a clear signal — as influence accumulates D
    approaches the raw ratio."""
    from scoring.signals import compute_D

    ig = InfluenceGraph(
        nodes={n: Node(n, NodeKind.SERVICE, n) for n in ("a", "b", "c", "d")},
        edges=[
            Edge("a", "b", CallType.SYNC, degraded=True),
            Edge("a", "c", CallType.SYNC, degraded=True),
            Edge("a", "d", CallType.SYNC, degraded=True),
        ],
    )
    anomaly = KernelAnomaly(
        anomaly_score={"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0},
        first_deviation_time={"a": 0.0, "b": 1.0, "c": 2.0, "d": 3.0},
        resource_deviation={},
    )
    assert compute_D("a", ig, anomaly)[0] > 0.75


def test_prior_is_configurable_at_call_time():
    """A default argument would freeze D_PRIOR at import, silently ignoring any
    override and making the prior look insensitive when it never varied."""
    from scoring.signals import compute_D

    ig, anomaly = _source_vs_root()
    low = compute_D("bystander", ig, anomaly, prior=0.1)[0]
    high = compute_D("bystander", ig, anomaly, prior=5.0)[0]
    assert low > high, "a larger prior must pull further toward neutral"
