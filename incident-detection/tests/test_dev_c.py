"""Lane tests for Dev C — contracts, adapters, eval, classify, remediation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.isolation import IsolationView
from adapters.logs import messages_from_fixture_logs
from classify.classifier import FaultClassifier, classify_candidate
from classify.confusion import score_classification
from contracts import (
    ABSTENTION_FLAG,
    DualInjectionGroundTruth,
    EnabledSignals,
    FaultClass,
    NodeKind,
    RankedCluster,
    RankedResult,
    Role,
    RootCauseCandidate,
    ScenarioId,
    SignalScores,
    Weights,
)
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
from remediation.router import RemediationRouter


def test_golden_fixture_eval_gate():
    out = run_golden_fixture()
    assert out["FixtureFull"]["metrics"]["top1"] == 1.0
    assert out["MostAnomalous"]["metrics"]["top1"] == 1.0
    assert out["FixtureFull"]["metrics"]["subgraph_recall"] == 1.0


def test_flat_ranked_result_from_dict():
    ranked = load_ranked_result()
    assert ranked.candidates
    assert ranked.candidates[0].node_id == "product-catalog"
    assert ranked.primary_candidates is ranked.candidates
    roundtrip = RankedResult.from_dict(ranked.to_dict())
    assert roundtrip.candidates[0].node_id == "product-catalog"


def test_flat_ab_shaped_json_without_clusters():
    raw = {
        "incident_id": "ab-shaped",
        "fault_class": "AppLogic",
        "abstain_reason": None,
        "candidates": [
            {
                "node_id": "product-catalog",
                "node_kind": "service",
                "score": 0.9,
                "confidence": 0.8,
                "role": "origin",
                "signals": {"T": 1.0, "D": 0.5, "P": 0.5},
                "dev_vector": {"error_rate": 0.9},
                "explanation": "origin",
            }
        ],
    }
    result = RankedResult.from_dict(raw)
    assert len(result.candidates) == 1
    assert result.fault_class == FaultClass.APP_LOGIC
    assert not result.abstained


def test_weights_signal_dict_roundtrip():
    w = Weights(w_T=0.5, w_D=0.3, w_P=0.2)
    assert w.to_signal_dict() == {"T": 0.5, "D": 0.3, "P": 0.2}
    assert Weights.from_signal_dict({"T": 0.5, "D": 0.3, "P": 0.2}) == w
    assert EnabledSignals.only("T").to_set() == {"T"}


def test_most_anomalous_orders_by_score():
    anomaly = load_anomaly()
    gt = load_ground_truth()
    result = most_anomalous_rank(gt.incident_id, gt.subgraph_node_ids, anomaly)
    ranked = result.ranked_node_ids()
    assert ranked[0] == "product-catalog"
    scores = [anomaly.score(n) for n in ranked]
    assert scores == sorted(scores, reverse=True)


def test_subgraph_recall_metric():
    assert subgraph_recall("product-catalog", ["cart", "product-catalog"]) is True
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
    for c in r.candidates:
        assert c.signals.D == 0.0
        assert c.signals.P == 0.0


def test_fixture_scorer_fail_closed_on_incident_id():
    scorer = FixtureScorer()
    with pytest.raises(ValueError, match="refuse silent reuse"):
        scorer.score({"incident_id": "not-golden"})


def test_fixture_ranked_fail_closed():
    runner = EvalRunner.from_golden()
    gt = load_ground_truth()
    bad = type(gt)(
        incident_id="other-id",
        true_root_node=gt.true_root_node,
        fault_class=gt.fault_class,
        inject_start=gt.inject_start,
        inject_end=gt.inject_end,
        scenario=gt.scenario,
        injection_site=gt.injection_site,
        subgraph_node_ids=list(gt.subgraph_node_ids),
    )
    runner.incidents = [(bad, load_anomaly(), load_subgraph())]
    with pytest.raises(ValueError, match="refuse silent reuse"):
        runner.evaluate_fixture_ranked()


def test_train_test_no_leakage():
    base = load_ground_truth()
    corpus = []
    for site in ("product-catalog", "kafka", "payment", "node-1"):
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
    subgraph = dict(load_subgraph())
    subgraph.pop("incident_id", None)
    corpus = []
    for i, site in enumerate(("product-catalog", "payment", "checkout", "kafka")):
        corpus.append(
            type(gt)(
                incident_id=f"inc-{i}",
                true_root_node="product-catalog",
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
        (scorer.score(subgraph, weights=result.best_weights), "product-catalog")
        for _ in split.train
    ]
    cal = fit_calibration(train_pairs)
    ranked = scorer.score(subgraph, weights=result.best_weights)
    apply_calibrated_confidence(ranked, cal)
    assert 0.0 <= ranked.candidates[0].confidence <= 1.0
    assert raw_confidence(ranked.candidates) >= 0.0


def test_classification_golden_applogic():
    ranked = load_ranked_result()
    logs_by_node = messages_from_fixture_logs(load_logs())
    clf = FaultClassifier(logs_by_node)
    pred = clf.classify(ranked.candidates[0])
    assert pred.fault_class == FaultClass.APP_LOGIC
    cm = score_classification([(FaultClass.APP_LOGIC, pred.fault_class)])
    assert cm.accuracy == 1.0


def test_classifier_fallback_without_logs():
    cand = RootCauseCandidate(
        node_id="db",
        node_kind=NodeKind.DEPENDENCY,
        score=0.5,
        confidence=0.1,
        role=Role.ORIGIN,
        signals=SignalScores(),
    )
    pred = classify_candidate(cand, [])
    assert pred.fault_class == FaultClass.DEPENDENCY


def test_timing_excluded_from_confusion():
    cm = score_classification(
        [
            (FaultClass.TIMING, FaultClass.DEPENDENCY),
            (FaultClass.DEPENDENCY, FaultClass.DEPENDENCY),
        ]
    )
    assert cm.n == 1
    assert cm.skipped_timing == 1


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
    clusters = [
        RankedCluster(
            fault_id="0",
            node_ids=[n for n, f in dual.node_to_fault.items() if f == 0],
            candidates=list(ranked.candidates),
        ),
        RankedCluster(
            fault_id="1",
            node_ids=[n for n, f in dual.node_to_fault.items() if f == 1],
            candidates=[
                RootCauseCandidate(
                    node_id="currency",
                    node_kind=NodeKind.SERVICE,
                    score=0.9,
                    confidence=0.5,
                    role=Role.ORIGIN,
                    signals=SignalScores(T=1.0),
                )
            ],
        ),
    ]
    view = IsolationView.from_ranked_clusters(ranked, clusters)
    metrics = evaluate_isolation([(dual, view)])
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
    abstained = IsolationView.abstain("d3", ABSTENTION_FLAG)
    m3 = evaluate_isolation([(d3, abstained)])
    assert m3.abstention_correct == 1.0


def test_remediation_three_deep_branches(monkeypatch):
    from remediation.apply import k8s as k8s_apply
    from remediation import router as router_mod

    monkeypatch.setattr(
        router_mod.runtime_profile,
        "supports_action",
        lambda _service, action: action != "restart_data_path",
    )
    monkeypatch.setattr(
        k8s_apply,
        "read_workload_revision",
        lambda *_a, **_k: {
            "kind": "Deployment",
            "revision": "2",
            "resource_version": "10",
            "images": ["example:v2"],
        },
    )
    fake_flag = {
        "resource_version": "10",
        "doc": {
            "flags": {
                "testFailure": {
                    "defaultVariant": "on",
                    "variants": {"on": True, "off": False},
                }
            }
        },
    }
    monkeypatch.setattr(k8s_apply, "read_flagd_config", lambda *_a, **_k: fake_flag)
    ranked = load_ranked_result()
    top = ranked.candidates[0]
    top.flags["flag_key"] = "testFailure"
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
    plans = [router.route(FaultClass.DEPENDENCY, c, ranked) for c in ranked.candidates[:2]]
    cli = ApprovalCLI(input_fn=lambda _: "approve")
    outcome = cli.present(ranked, plans)
    assert outcome.decision == ApprovalDecision.APPROVE
    assert outcome.candidate_index == 0


def test_approval_cli_reject_then_approve():
    ranked = load_ranked_result()
    router = RemediationRouter()
    plans = [router.route(FaultClass.DEPENDENCY, c, ranked) for c in ranked.candidates[:2]]
    answers = iter(["reject", "approve"])
    cli = ApprovalCLI(input_fn=lambda _: next(answers))
    outcome = cli.present(ranked, plans)
    assert outcome.decision == ApprovalDecision.APPROVE
    assert outcome.candidate_index == 1


def test_typed_plan_and_policy():
    from remediation.plan_schema import ExactTarget, PlanChange, SLOGate, TypedPlanSpec
    from remediation.policy import check_plan_executable, kill_switch_active

    spec = TypedPlanSpec(
        action="config_patch",
        fault_class="Config",
        target=ExactTarget(
            cluster="kind",
            namespace="otel-demo",
            kind="ConfigMap",
            name="flagd-config",
            config_key="demo.flagd.json#flags.x.defaultVariant",
        ),
        before_fingerprint="abc",
        change=PlanChange(
            description="set flag",
            patch={"flag_key": "x", "defaultVariant": "off", "resource_version": "1"},
            inverse={"flag_key": "x", "defaultVariant": "on", "resource_version": "1"},
        ),
        risk_tier="low",
        required_approvals=1,
        slo_gates=SLOGate(require_fresh=False),
        incident_id="inc-1",
        candidate_node="flagd",
        confidence=0.8,
    )
    assert check_plan_executable(spec).allowed
    assert kill_switch_active().allowed


def test_apply_plan_v1_disabled():
    from remediation.service import apply_plan_v1

    out = apply_plan_v1({"action": "config_patch"})
    assert out["attempted"] is False
    assert out["mode"] == "worker_required"


def test_compute_metrics_per_scenario():
    cases = [
        ("product-catalog", ["product-catalog", "cart"], ["product-catalog", "cart"], "S1"),
        ("kafka", ["cart", "kafka"], ["cart", "kafka"], "S4"),
    ]
    m = compute_metrics(cases)
    assert m.n == 2
    assert "S1" in m.per_scenario
    assert m.per_scenario["S1"].top1 == 1.0


def test_incident_fault_class_string_tolerant():
    gt = load_ground_truth()
    raw = gt.to_dict()
    raw["fault_class"] = "AppLogic"
    again = type(gt).from_dict(raw)
    assert again.fault_class_value() == "AppLogic"


def test_ab_scorer_top1_on_golden():
    from adapters.scorer import ABScorer
    runner = EvalRunner.from_golden()
    report = runner.evaluate_scorer(ABScorer(), method="ABFull")
    assert report.metrics.subgraph_recall == 1.0
    assert report.metrics.top1 == 1.0


def test_isolation_result_maps_to_view():
    from adapters.isolation import IsolationView
    class Fake:
        clusters = [{"product-catalog", "checkout"}, {"currency"}]
        ranked_by_cluster = {}
        abstain_reason = None
    view = IsolationView.from_isolation_result(Fake(), incident_id="x")
    assert view.cluster_count == 2
    abstained = IsolationView.from_isolation_result(
        type("A", (), {"clusters": [], "ranked_by_cluster": {}, "abstain_reason": ABSTENTION_FLAG})(),
        incident_id="y",
    )
    assert abstained.abstained
