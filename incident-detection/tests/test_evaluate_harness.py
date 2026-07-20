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


def test_baseline_every_scored_scenario_names_a_true_root():
    """Pins the baseline so a scoring change shows up as a diff, not a vibe."""
    results = _by_id(run_all())
    for sid in ("S0", "S1", "S2", "S5", "D1", "D2"):
        assert results[sid].top1, f"{sid} regressed off top-1"
    assert results["D3"].abstained and results["D3"].abstention_ok


def test_baseline_every_true_root_tops_its_cluster():
    """Stronger than top-1 for dual scenarios: finding one of two faults is half
    a result, and top-1 alone cannot express that."""
    for result in run_all():
        if result.abstained:
            continue
        assert result.roots_found == 1.0, (
            f"{result.scenario}: only {result.roots_found:.0%} of true roots "
            f"topped a cluster (tops={result.cluster_tops})"
        )


def test_known_gap_svc_a_tops_its_cluster_but_is_not_labelled_origin():
    """A real remaining gap, pinned so it is not mistaken for correctness.

    D2's svc-a wins its cluster yet carries D=0.494 — below derive_role's 0.55
    ORIGIN threshold — so it is labelled amplifier and omitted from
    origin_candidates. `resolve_origins` deliberately does not promote a top
    candidate whose own evidence does not support ORIGIN. Whether the threshold
    is right is a tuning question that needs real data, not a fixture.
    """
    d2 = _by_id(run_all())["D2"]
    assert "svc-a" in d2.cluster_tops
    assert "svc-a" not in d2.origins
    assert d2.origin_recall == 0.5


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


def test_shared_dependency_no_longer_outranks_the_true_root():
    """The shared-dependency confound, which is what D's smoothing exists for.

    shared-db has zero in-degree in the influence graph, so before smoothing it
    scored D=1.0 — maximum directional evidence — despite deviating 20s AFTER
    svc-a and causing nothing. It outranked the true root and was published as
    an origin. That is the shape of every shared database, cache, queue, and
    auth service, so the failure generalizes well beyond this fixture.
    """
    results = _by_id(run_all())
    d2 = results["D2"]
    assert d2.cluster_count_ok
    assert d2.roots_in_distinct_clusters is True
    assert "shared-db" not in d2.origins, "a non-cause published as an origin"
    assert d2.origin_precision == 1.0


@pytest.mark.parametrize("subset", [{"T"}, {"D"}, {"P"}, {"T", "D"}])
def test_ablation_runs_for_every_signal_subset(subset):
    results = run_all(enabled_signals=subset)
    assert len(results) == len(FIXTURES)


def test_weights_reach_the_scoring_path():
    """The sweep is only meaningful if weights actually propagate through
    isolate() into score(). Compared on configs the corpus can still separate."""
    t_only = summarize(run_all(weights={"T": 1.0, "D": 0.0, "P": 0.0}))
    d_only = summarize(run_all(weights={"T": 0.0, "D": 1.0, "P": 0.0}))
    assert t_only["top1"] != d_only["top1"]


def test_corpus_is_saturated_for_weight_tuning():
    """DOCUMENTS A LIMIT, not a success.

    Every weighting that includes T now scores top1=1.00 / MRR=1.000, so the
    aggregate cannot rank one configuration above another. The harness still
    detects REGRESSIONS (a change that breaks a scenario shows up immediately),
    but it can no longer GUIDE tuning — there is no gradient left to follow.

    Anything further on weights needs data this corpus cannot supply: real chaos
    runs joined to groundtruth.ground_truth via causa_eval. If this test starts
    failing because a config drops below 1.00, that is a regression, not the
    corpus regaining discriminative power.
    """
    balanced = summarize(run_all(weights={"T": 1 / 3, "D": 1 / 3, "P": 1 / 3}))
    t_only = summarize(run_all(weights={"T": 1.0, "D": 0.0, "P": 0.0}))
    t_heavy = summarize(run_all(weights={"T": 0.6, "D": 0.2, "P": 0.2}))
    assert balanced["top1"] == t_only["top1"] == t_heavy["top1"] == 1.0
    assert balanced["mrr"] == t_only["mrr"] == t_heavy["mrr"] == 1.0
