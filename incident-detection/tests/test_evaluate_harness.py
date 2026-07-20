"""
Guards on the offline accuracy harness.

The harness exists to make scoring changes measurable, so its own metrics must
be trustworthy: it has to run the production path, score abstention honestly,
and not quietly reward an engine that answers nothing.
"""

from __future__ import annotations

import pytest

from testbed.harness.evaluate import (
    FIXTURES,
    ScenarioResult,
    evaluate_scenario,
    run_all,
    summarize,
)


def _by_id(results):
    return {r.scenario: r for r in results}


def test_every_fixture_is_evaluable():
    results = run_all()
    assert len(results) == len(FIXTURES)
    assert {r.scenario for r in results} == {sid for sid, _, _ in FIXTURES}


def test_baseline_top1_and_known_failure():
    """Pins the current baseline so a scoring change shows up as a diff rather
    than a vibe. D2 is a KNOWN failure — the shared-dependency confound — and is
    asserted as failing so a fix registers loudly instead of silently."""
    results = _by_id(run_all())
    for sid in ("S0", "S1", "S2", "S5", "D1"):
        assert results[sid].top1, f"{sid} regressed off top-1"
    assert not results["D2"].top1, (
        "D2 now passes — that is good news; update this baseline deliberately"
    )
    assert results["D3"].abstained and results["D3"].abstention_ok


def test_abstention_is_excluded_from_ranking_but_scored_separately():
    """An engine that abstains on everything must not score 1.0."""
    always_abstains = [
        ScenarioResult(scenario=f"X{i}", true_root="r", abstained=True)
        for i in range(4)
    ]
    summary = summarize(always_abstains)
    assert summary["scored"] == 0
    assert summary["top1"] == 0.0
    # All four abstained but none was expected to → abstention accuracy is 0.
    assert summary["abstention"] == 0.0


def test_rank_is_none_when_true_root_is_absent_from_candidates():
    r = ScenarioResult(scenario="X", true_root="ghost", rank=None)
    assert r.reciprocal_rank == 0.0


def test_reciprocal_rank_matches_position():
    assert ScenarioResult("X", "r", rank=1).reciprocal_rank == 1.0
    assert ScenarioResult("X", "r", rank=2).reciprocal_rank == 0.5
    assert ScenarioResult("X", "r", rank=4).reciprocal_rank == 0.25


def test_origin_precision_and_recall():
    r = ScenarioResult(
        scenario="X", true_root="a",
        origins=["a", "noise"], origin_truth=["a", "b"],
    )
    assert r.origin_precision == 0.5     # 1 of 2 predicted are real
    assert r.origin_recall == 0.5        # 1 of 2 real were found
    assert not r.origin_exact


def test_fault_class_declined_is_a_miss_not_an_error():
    """None means the kernel refused to classify. It must not count as correct,
    and must not count as a wrong answer either."""
    r = ScenarioResult(
        scenario="X", true_root="a", fault_class=None, fault_class_truth="Infra"
    )
    assert r.fault_class_ok is None
    summary = summarize([r])
    assert summary["fault_class_coverage"] == 0.0


def test_d2_isolation_is_correct_and_the_failure_is_purely_ranking():
    """Pins WHERE D2 goes wrong, so a future fix targets the right layer.

    Isolation does its job: svc-a and svc-b land in distinct clusters, and
    shared-db — genuinely connected to both — is placed with one of them, which
    is reasonable. The failure is entirely inside cluster-0's ranking, where
    shared-db (D=1.0 purely for having zero in-degree, and deviating 20s LAST)
    outscores svc-a. Fixing the clustering would not help; fixing D would.
    """
    results = _by_id(run_all())
    d2 = results["D2"]
    assert d2.cluster_count_ok
    assert d2.roots_in_distinct_clusters is True, "isolation separates the roots"
    assert not d2.top1, "yet the merged ranking still names the wrong root"
    assert "shared-db" in d2.origins, "a non-cause is published as an origin"


@pytest.mark.parametrize("subset", [{"T"}, {"D"}, {"P"}, {"T", "D"}])
def test_ablation_runs_for_every_signal_subset(subset):
    results = run_all(enabled_signals=subset)
    assert len(results) == len(FIXTURES)


def test_weights_change_the_outcome():
    """If weights had no effect the sweep would be meaningless."""
    balanced = summarize(run_all(weights={"T": 1 / 3, "D": 1 / 3, "P": 1 / 3}))
    t_only = summarize(run_all(weights={"T": 1.0, "D": 0.0, "P": 0.0}))
    assert balanced != t_only
