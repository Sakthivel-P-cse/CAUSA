"""Scorer ablation — all 7 non-empty subsets of {T,D,P}."""

from itertools import combinations

from fixtures.incident_s0 import load_incident_s0
from graphs.incident_subgraph import build_incident_subgraph
from graphs.influence_graph import build_influence_graph
from scoring.scorer import Scorer, score
from scoring.signals import compute_signals

SIGNALS = ("T", "D", "P")


def test_all_seven_nonempty_subsets():
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    ig = build_influence_graph(sub)
    # Same normalization basis the scorer uses, or the arithmetic check compares
    # signals computed over two different bases (see IncidentSubgraph.seed_ids).
    sigs = compute_signals(ig, anomaly, seed_ids=sub.seed_ids)

    for r in range(1, 4):
        for subset in combinations(SIGNALS, r):
            enabled = set(subset)
            # Pass non-zero weights even for disabled — scorer must zero them.
            weights = {"T": 0.5, "D": 0.3, "P": 0.2}
            result = score(
                sub,
                weights=weights,
                enabled_signals=enabled,
                ig=ig,
                anomaly=anomaly,
                run_arbitration=False,
            )
            assert result.candidates
            for c in result.candidates:
                ns = sigs.by_node[c.node_id]
                expected = 0.0
                if "T" in enabled:
                    expected += weights["T"] * ns.T
                if "D" in enabled:
                    expected += weights["D"] * ns.D
                if "P" in enabled:
                    expected += weights["P"] * ns.P
                assert abs(c.score - expected) < 1e-9, (
                    f"subset={enabled} node={c.node_id}: {c.score} != {expected}"
                )


def test_scorer_class_matches_function():
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    a = score(sub, enabled_signals={"T", "D"})
    b = Scorer().score(sub, enabled_signals={"T", "D"})
    assert [c.node_id for c in a.candidates] == [c.node_id for c in b.candidates]
    assert [c.score for c in a.candidates] == [c.score for c in b.candidates]


def test_deterministic_repeated_calls():
    topology, traces, anomaly, _ = load_incident_s0()
    sub = build_incident_subgraph(topology, traces, anomaly)
    r1 = score(sub)
    r2 = score(sub)
    assert [(c.node_id, c.score, c.T, c.D, c.P) for c in r1.candidates] == [
        (c.node_id, c.score, c.T, c.D, c.P) for c in r2.candidates
    ]
