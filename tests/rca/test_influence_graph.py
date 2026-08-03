"""Influence Graph orientation tests — must pass before any signal code is trusted."""

from contracts import CallType, NodeKind
from fixtures.incident_s0 import load_incident_s0
from graphs.incident_subgraph import build_incident_subgraph
from graphs.influence_graph import build_influence_graph, orient_edge
from graphs.types import Edge


def test_orient_sync_reverses():
    e = Edge("caller", "callee", CallType.SYNC, degraded=True)
    o = orient_edge(e)
    assert o.src == "callee"
    assert o.dst == "caller"
    assert o.call_type == CallType.SYNC
    assert o.degraded is True


def test_orient_async_keeps():
    e = Edge("producer", "consumer", CallType.ASYNC)
    o = orient_edge(e)
    assert o.src == "producer"
    assert o.dst == "consumer"
    assert o.call_type == CallType.ASYNC


def test_orient_runs_on_infra_to_service():
    e = Edge("node-a", "cart-service", CallType.RUNS_ON)
    o = orient_edge(e)
    assert o.src == "node-a"
    assert o.dst == "cart-service"
    assert o.call_type == CallType.RUNS_ON


def test_db_leaf_gains_outgoing_ig_edges():
    """cart-db is a sync sink in the call graph; in IG it must influence callers."""
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    ig = build_influence_graph(sub)

    assert "cart-db" in ig.nodes
    out = ig.successors("cart-db")
    assert "cart-service" in out, (
        f"Expected cart-db → cart-service in IG, got out={out}, edges={ig.edges}"
    )
    # Original call edge was cart-service → cart-db; that direction must NOT remain.
    assert "cart-db" not in ig.successors("cart-service") or CallType.SYNC not in {
        e.call_type for e in ig.out_edges("cart-service") if e.dst == "cart-db"
    }
    sync_to_db = [
        e for e in ig.edges
        if e.src == "cart-service" and e.dst == "cart-db" and e.call_type == CallType.SYNC
    ]
    assert sync_to_db == [], "sync call-graph direction must be reversed in IG"


def test_ig_unifies_infra_and_service():
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    ig = build_influence_graph(sub)
    kinds = {n.kind for n in ig.nodes.values()}
    assert NodeKind.SERVICE in kinds
    assert NodeKind.INFRA in kinds
    # runs-on preserved as infra → service
    runs = [e for e in ig.edges if e.call_type == CallType.RUNS_ON]
    assert any(e.src == "node-a" and e.dst == "cart-service" for e in runs)
