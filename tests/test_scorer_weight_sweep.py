"""Weight-sweep sanity for Dev C's C4 harness — not a full eval."""

from fixtures.incident_s0 import load_incident_s0
from graphs.incident_subgraph import build_incident_subgraph
from graphs.influence_graph import build_influence_graph
from scoring.scorer import score
from scoring.signals import compute_signals


def test_increasing_T_weight_raises_high_T_node_score():
    """Monotonic response: bumping w_T increases score of the earliest origin."""
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    ig = build_influence_graph(sub)
    sigs = compute_signals(ig, anomaly)
    origin = "cart-db"
    assert sigs.by_node[origin].T > sigs.by_node["frontend"].T

    low = score(
        sub,
        weights={"T": 0.2, "D": 0.4, "P": 0.4},
        ig=ig,
        run_arbitration=False,
    )
    high = score(
        sub,
        weights={"T": 0.8, "D": 0.1, "P": 0.1},
        ig=ig,
        run_arbitration=False,
    )
    by_low = {c.node_id: c.score for c in low.candidates}
    by_high = {c.node_id: c.score for c in high.candidates}
    assert by_high[origin] > by_low[origin]


def test_disabled_signal_zeros_contribution():
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    ig = build_influence_graph(sub)
    sigs = compute_signals(ig, anomaly)

    result = score(
        sub,
        weights={"T": 1.0, "D": 1.0, "P": 1.0},
        enabled_signals={"T"},
        ig=ig,
        run_arbitration=False,
    )
    for c in result.candidates:
        assert abs(c.score - sigs.by_node[c.node_id].T) < 1e-9
