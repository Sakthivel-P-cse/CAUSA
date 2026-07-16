"""Comprehensive tests for Dev C lane — contracts, eval, classify, remediation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from classify.classifier import FaultClassifier
from classify.confusion import score_classification
from contracts.c3_candidates import FaultClass, NodeKind, RankedResult
from contracts.c4_scorer import EnabledSignals, Weights
from contracts.c5_ground_truth import DualInjectionGroundTruth, ScenarioId
from eval.ablation import run_ablation
from eval.baselines import most_anomalous_rank
from eval.calibration import apply_calibrated_confidence, fit_calibration, raw_confidence
from eval.fixture_scorer import FixtureScorer
from eval.isolation_metrics import adjusted_rand_index, evaluate_isolation
from eval.metrics import compute_metrics, subgraph_recall
from eval.runner import EvalRunner, run_golden_fixture
from eval.splits import scenario_train_test_split
from eval.tuning import tune_weights
from fixtures import (
    load_anomaly,
    load_dual_ground_truth,
    load_ground_truth,
    load_logs,
    load_ranked_result,
    load_subgraph,
)
from remediation.approval_cli import ApprovalCLI, ApprovalDecision
from remediation.canary import SimulatedCanary
from remediation.router import RemediationRouter


def test_golden_fixture_eval_gate():
    out = run_golden_fixture()
    assert out["FixtureFull"]["metrics"]["top1"] == 1.0
    assert out["MostAnomalous"]["metrics"]["top1"] == 1.0
    assert out["FixtureFull"]["metrics"]["subgraph_recall"] == 1.0


def test_most_anomalous_orders_by_score():
    anomaly = load_anomaly()
    gt = load_ground_truth()
    result = most_anomalous_rank(gt.incident_id, gt.subgraph_node_ids, anomaly)
    ranked = result.ranked_node_ids()
    assert ranked[0] == "cart-redis"
    scores = [anomaly.score(n) for n in ranked]
    assert scores == sorted(scores, reverse=True)


def test_subgraph_recall_metric():
    assert subgraph_recall("cart-redis", ["cart", "cart-redis"]) is True
    assert subgraph_recall("missing", ["cart"]) is False


def test_ablation_via_fixture_scorer():
    runner = EvalRunner.from_golden()
    table = run_ablation(runner, FixtureScorer())
    assert "Full" in table.rows
    assert "EarliestOnly" in table.rows
    assert "MostAnomalous" in table.rows
    assert table.rows["Full"].metrics.top1 == 1.0


def test_enabled_signals_only_t():
    scorer = FixtureScorer()
    r = scorer.score({}, enabled_signals=EnabledSignals.only("T"))
    # All D/P zeroed
    for c in r.primary_candidates:
        assert c.signals.D == 0.0
        assert c.signals.P == 0.0


def test_train_test_no_leakage():
    base = load_ground_truth()
    corpus = []
    for site in ("cart-redis", "kafka", "payment", "node-1"):
        for sc in (ScenarioId.S1, ScenarioId.S4):
            corpus.append(
                type(base)(
                    incident_id=f"{sc.value}-{site}",
                    true_root_node=base.true_root_node,
                    fault_class=base.fault_class,
                    inject_start=base.inject_start,
                    inject_end=base.inject_end,
                    scenario=sc,
                    injection_site=site,
                    subgraph_node_ids=list(base.subgraph_node_ids),
                )
            )
    split = scenario_train_test_split(corpus, held_out_sites=["kafka"], seed=1)
    split.assert_no_leakage()
    train_sites = {i.injection_site for i in split.train}
    assert "kafka" not in train_sites


def test_tuning_and_calibration():
    gt = load_ground_truth()
    anomaly = load_anomaly()
    subgraph = load_subgraph()
    corpus = []
    for i, site in enumerate(("cart-redis", "payment", "checkout", "kafka")):
        corpus.append(
            type(gt)(
                incident_id=f"inc-{i}",
                true_root_node="cart-redis",
                fault_class=gt.fault_class,
                inject_start=gt.inject_start,
                inject_end=gt.inject_end,
                scenario=ScenarioId.S1,
                injection_site=site,
                subgraph_node_ids=list(gt.subgraph_node_ids),
            )
        )
    split = scenario_train_test_split(corpus, test_ratio=0.25, held_out_sites=["kafka"], seed=0)
    scorer = FixtureScorer()
    anomaly_by_id = {i.incident_id: anomaly for i in corpus}
    subgraph_by_id = {i.incident_id: subgraph for i in corpus}
    result = tune_weights(scorer, split, anomaly_by_id, subgraph_by_id, steps=(0.0, 1.0))
    assert result.verified_no_leakage
    assert result.test_metrics.top1 == 1.0

    train_pairs = [
        (scorer.score(subgraph, weights=result.best_weights), "cart-redis")
        for _ in split.train
    ]
    cal = fit_calibration(train_pairs)
    ranked = scorer.score(subgraph, weights=result.best_weights)
    apply_calibrated_confidence(ranked, cal)
    assert 0.0 <= ranked.primary_candidates[0].confidence <= 1.0
    assert raw_confidence(ranked.primary_candidates) >= 0.0


def test_classification_golden_dependency():
    ranked = load_ranked_result()
    logs = load_logs()
    logs_by_node = {k: [e["message"] for e in v] for k, v in logs.items()}
    clf = FaultClassifier(logs_by_node)
    pred = clf.classify(ranked.primary_candidates[0])
    assert pred.fault_class == FaultClass.DEPENDENCY
    cm = score_classification([(FaultClass.DEPENDENCY, pred.fault_class)])
    assert cm.accuracy == 1.0


def test_confusion_hard_pairs_visible():
    cm = score_classification(
        [
            (FaultClass.DEPENDENCY, FaultClass.RESOURCE),
            (FaultClass.CONFIG, FaultClass.APP_LOGIC),
        ]
    )
    hard = cm.hard_confusions()
    assert any(a == "Dependency" and b == "Resource" for a, b, _ in hard)
    assert any(a == "Config" and b == "AppLogic" for a, b, _ in hard)


def test_isolation_ari_and_abstention():
    assert adjusted_rand_index({"a": 0, "b": 0, "c": 1}, {"a": 0, "b": 0, "c": 1}) == pytest.approx(1.0)
    dual = load_dual_ground_truth()
    ranked = load_ranked_result()
    from contracts.c3_candidates import RankedCluster

    result = RankedResult(
        incident_id=dual.incident_id,
        clusters=[
            RankedCluster(
                fault_id="0",
                node_ids=[n for n, f in dual.node_to_fault.items() if f == 0],
                candidates=list(ranked.primary_candidates),
            ),
            RankedCluster(
                fault_id="1",
                node_ids=[n for n, f in dual.node_to_fault.items() if f == 1],
                candidates=[c for c in ranked.primary_candidates if c.node_id == "payment"]
                or list(ranked.primary_candidates[-1:]),
            ),
        ],
    )
    # Force payment as top of cluster 1
    result.clusters[1].candidates[0].node_id = "payment"
    metrics = evaluate_isolation([(dual, result)])
    assert metrics.cluster_count_accuracy == 1.0
    assert metrics.ari > 0.5

    d3 = DualInjectionGroundTruth(
        incident_id="d3",
        true_root_nodes=["a", "b"],
        fault_classes=[FaultClass.DEPENDENCY, FaultClass.APP_LOGIC],
        node_to_fault={"a": 0, "b": 1},
        inject_start=dual.inject_start,
        inject_end=dual.inject_end,
        scenario=ScenarioId.D3,
        expect_abstention=True,
    )
    abstained = RankedResult(incident_id="d3", abstained=True, abstention_reason="MULTI-INCIDENT — ambiguous")
    m3 = evaluate_isolation([(d3, abstained)])
    assert m3.abstention_correct == 1.0


def test_remediation_three_deep_branches():
    ranked = load_ranked_result()
    top = ranked.primary_candidates[0]
    router = RemediationRouter()

    dep = router.route(FaultClass.DEPENDENCY, top, ranked)
    assert dep.depth == "deep"
    assert dep.action == "rollback_to_last_good"

    cfg = router.route(FaultClass.CONFIG, top, ranked)
    assert cfg.depth == "deep"
    assert "before" in cfg.details["patch"]

    app = router.route(FaultClass.APP_LOGIC, top, ranked)
    assert app.depth == "deep"
    assert app.details["pr"]["merged"] is False

    stub = router.route(FaultClass.DATA, top, ranked)
    assert stub.depth == "stub"


def test_approval_cli_approve_first():
    ranked = load_ranked_result()
    router = RemediationRouter()
    plans = [
        router.route(FaultClass.DEPENDENCY, c, ranked) for c in ranked.primary_candidates[:2]
    ]
    cli = ApprovalCLI(input_fn=lambda _: "approve")
    outcome = cli.present(ranked, plans)
    assert outcome.decision == ApprovalDecision.APPROVE
    assert outcome.candidate_index == 0


def test_approval_cli_reject_then_approve():
    ranked = load_ranked_result()
    router = RemediationRouter()
    plans = [
        router.route(FaultClass.DEPENDENCY, c, ranked) for c in ranked.primary_candidates[:2]
    ]
    answers = iter(["reject", "approve"])
    cli = ApprovalCLI(input_fn=lambda _: next(answers))
    outcome = cli.present(ranked, plans)
    assert outcome.decision == ApprovalDecision.APPROVE
    assert outcome.candidate_index == 1


def test_canary_rollback_on_slo_breach():
    ok = SimulatedCanary().apply()
    assert ok.applied and not ok.rolled_back

    bad = SimulatedCanary(metric_fn=SimulatedCanary.breaching_metrics).apply()
    assert bad.rolled_back
    assert not bad.applied
    assert any(s.breached for s in bad.snapshots)


def test_compute_metrics_per_scenario():
    cases = [
        ("cart-redis", ["cart-redis", "cart"], ["cart-redis", "cart"], "S1"),
        ("kafka", ["cart", "kafka"], ["cart", "kafka"], "S4"),
    ]
    m = compute_metrics(cases)
    assert m.n == 2
    assert "S1" in m.per_scenario
    assert m.per_scenario["S1"].top1 == 1.0
