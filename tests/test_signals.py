"""T/D/P signal tests against fixture S0."""

from fixtures.incident_s0 import load_incident_s0
from graphs.incident_subgraph import build_incident_subgraph
from graphs.influence_graph import build_influence_graph
from scoring.signals import compute_signals


def _s0_signals():
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    ig = build_influence_graph(sub)
    return compute_signals(ig, anomaly), anomaly, ig


def test_T_uses_time_delta_origin_high():
    signals, _, _ = _s0_signals()
    # cart-db at t=0 should have the highest T
    assert signals.by_node["cart-db"].T > signals.by_node["frontend"].T
    assert signals.by_node["cart-db"].T > 0.9


def test_slow_burn_T_is_exactly_half():
    signals, _, _ = _s0_signals()
    notifier = signals.by_node["notifier"]
    assert notifier.T == 0.5
    assert notifier.flags.get("slow_burn_T") is True


def test_D_origin_high_while_victims_ramp():
    """α self-term lets cart-db score high D even as callers are also anomalous."""
    signals, _, ig = _s0_signals()
    db = signals.by_node["cart-db"]
    assert db.D > 0.5
    assert ig.out_degree("cart-db") > 0
    # Victim/caller should have lower D than the DB origin.
    assert db.D > signals.by_node["frontend"].D


def test_role_origin_on_s0():
    signals, _, _ = _s0_signals()
    assert signals.by_node["cart-db"].role.value == "origin"


def test_P_zero_outdegree_flagged():
    signals, _, ig = _s0_signals()
    # frontend in IG: sync edges reverse toward it from callees, so it may
    # have out-edges via async path... payment→notifier stays producer→consumer.
    # Find a node with zero out-degree if any.
    zero_out = [nid for nid in ig.nodes if ig.out_degree(nid) == 0]
    for nid in zero_out:
        sig = signals.by_node[nid]
        assert sig.P == 0.0
        assert sig.flags.get("zero_outdegree_P") is True
