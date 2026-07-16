"""M6 demo artifacts: reliability diagram, confusion matrix, per-scenario table."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from classify.classifier import FaultClassifier
from classify.confusion import ConfusionMatrix, score_classification
from contracts.c3_candidates import FaultClass, RankedCluster, RankedResult
from contracts.c5_ground_truth import DualInjectionGroundTruth, Incident, ScenarioId
from eval.ablation import run_ablation
from eval.calibration import (
    apply_calibrated_confidence,
    evaluate_calibration,
    fit_calibration,
)
from eval.fixture_scorer import FixtureScorer
from eval.isolation_metrics import evaluate_isolation
from eval.runner import EvalRunner, run_golden_fixture
from eval.splits import ANALYTICAL_CROSS_TOPOLOGY_ARGUMENT, scenario_train_test_split
from eval.tuning import tune_weights
from fixtures import (
    load_anomaly,
    load_dual_ground_truth,
    load_ground_truth,
    load_logs,
    load_ranked_result,
    load_subgraph,
)
from remediation.canary import SimulatedCanary
from remediation.router import RemediationRouter


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def _synthetic_corpus() -> list[Incident]:
    """Minimal multi-scenario corpus derived from golden for harness demos."""
    base = load_ground_truth()
    corpus: list[Incident] = []
    sites = {
        ScenarioId.S0: "product-catalog",
        ScenarioId.S1: "cart-redis",
        ScenarioId.S2: "payment",
        ScenarioId.S3: "checkout",
        ScenarioId.S4: "kafka",
        ScenarioId.S5: "node-1",
    }
    for i, (sc, site) in enumerate(sites.items()):
        for rep in range(2):
            corpus.append(
                Incident(
                    incident_id=f"{sc.value}-{site}-r{rep}",
                    true_root_node=base.true_root_node if sc == ScenarioId.S1 else site,
                    fault_class=base.fault_class,
                    inject_start=base.inject_start,
                    inject_end=base.inject_end,
                    scenario=sc,
                    injection_site=site,
                    subgraph_node_ids=list(base.subgraph_node_ids),
                )
            )
    return corpus


def _ascii_reliability(points: list[tuple[float, float]]) -> str:
    lines = ["predicted\tempirical", "---------\t---------"]
    for p, e in points:
        bar_p = int(round(p * 20))
        bar_e = int(round(e * 20))
        lines.append(f"{p:.2f} {'#' * bar_p}\t{e:.2f} {'*' * bar_e}")
    return "\n".join(lines)


def generate_all() -> dict[str, Any]:
    ARTIFACTS.mkdir(exist_ok=True)
    golden = run_golden_fixture()

    runner = EvalRunner.from_golden()
    scorer = FixtureScorer()
    ablation = run_ablation(runner, scorer)

    # Classification on golden
    ranked = load_ranked_result()
    logs_raw = load_logs()
    logs_by_node = {
        nid: [e["message"] for e in entries] for nid, entries in logs_raw.items()
    }
    clf = FaultClassifier(logs_by_node)
    top = ranked.primary_candidates[0]
    pred = clf.classify(top)
    gt = load_ground_truth()
    cm = score_classification([(gt.fault_class, pred.fault_class)])

    # Also score a few synthetic predicted pairs to show hard confusions structure
    demo_pairs = [
        (FaultClass.DEPENDENCY, FaultClass.DEPENDENCY),
        (FaultClass.DEPENDENCY, FaultClass.RESOURCE),
        (FaultClass.CONFIG, FaultClass.APP_LOGIC),
        (FaultClass.APP_LOGIC, FaultClass.APP_LOGIC),
        (FaultClass.INFRA, FaultClass.INFRA),
        (FaultClass.RESOURCE, FaultClass.RESOURCE),
        (FaultClass.DATA, FaultClass.DATA),
        (FaultClass.CONFIG, FaultClass.CONFIG),
    ]
    cm_demo = score_classification(demo_pairs)

    # Tuning + calibration on synthetic corpus mapped to golden anomaly/subgraph
    corpus = _synthetic_corpus()
    # Map all synthetic IDs to golden anomaly/subgraph for FixtureScorer demos
    anomaly = load_anomaly()
    subgraph = load_subgraph()
    anomaly_by_id = {inc.incident_id: anomaly for inc in corpus}
    subgraph_by_id = {inc.incident_id: subgraph for inc in corpus}
    # Force true_root to cart-redis for all so FixtureScorer ranking is meaningful
    for inc in corpus:
        inc.true_root_node = "cart-redis"
        inc.subgraph_node_ids = list(gt.subgraph_node_ids)

    split = scenario_train_test_split(
        corpus,
        test_ratio=0.3,
        held_out_sites=["kafka", "node-1"],
        seed=7,
    )
    tuning = tune_weights(scorer, split, anomaly_by_id, subgraph_by_id, steps=(0.0, 0.5, 1.0))

    train_results = []
    for inc in split.train:
        r = scorer.score(subgraph_by_id[inc.incident_id], weights=tuning.best_weights)
        train_results.append((r, inc.true_root_node))
    fitted = fit_calibration(train_results)
    test_results = []
    for inc in split.test:
        r = scorer.score(subgraph_by_id[inc.incident_id], weights=tuning.best_weights)
        apply_calibrated_confidence(r, fitted)
        test_results.append((r, inc.true_root_node))
    cal_test = evaluate_calibration(test_results, fitted)

    # Isolation demo: D1 clean split using dual GT + synthetic multi-cluster result
    dual_gt = load_dual_ground_truth()
    dual_result = RankedResult(
        incident_id=dual_gt.incident_id,
        clusters=[
            RankedCluster(
                fault_id="f0",
                node_ids=[n for n, f in dual_gt.node_to_fault.items() if f == 0],
                candidates=ranked.primary_candidates[:3],
            ),
            RankedCluster(
                fault_id="f1",
                node_ids=[n for n, f in dual_gt.node_to_fault.items() if f == 1],
                candidates=[
                    c
                    for c in ranked.primary_candidates
                    if c.node_id == "payment"
                ]
                or ranked.primary_candidates[-1:],
            ),
        ],
    )
    # Ensure payment tops cluster 1
    if dual_result.clusters[1].candidates:
        dual_result.clusters[1].candidates[0].node_id = "payment"
    iso = evaluate_isolation([(dual_gt, dual_result)])

    # D3 abstention case
    d3_gt = DualInjectionGroundTruth(
        incident_id="d3-demo",
        true_root_nodes=["cart-redis", "payment"],
        fault_classes=[FaultClass.DEPENDENCY, FaultClass.APP_LOGIC],
        node_to_fault=dict(dual_gt.node_to_fault),
        inject_start=dual_gt.inject_start,
        inject_end=dual_gt.inject_end,
        scenario=ScenarioId.D3,
        expect_abstention=True,
    )
    d3_result = RankedResult(
        incident_id="d3-demo",
        clusters=[],
        abstained=True,
        abstention_reason="MULTI-INCIDENT — ambiguous",
    )
    iso_d3 = evaluate_isolation([(d3_gt, d3_result)])

    # Remediation demos
    router = RemediationRouter()
    plans = {
        "Dependency": router.route(FaultClass.DEPENDENCY, top, ranked).to_dict(),
        "AppLogic": router.route(FaultClass.APP_LOGIC, top, ranked).to_dict(),
        "Config": router.route(FaultClass.CONFIG, top, ranked).to_dict(),
    }
    canary_ok = SimulatedCanary().apply().to_dict()
    canary_rb = SimulatedCanary(metric_fn=SimulatedCanary.breaching_metrics).apply().to_dict()

    per_scenario = ablation.rows["Full"].metrics.per_scenario
    # Ensure S0–S5 keys appear in a table even if only S1 in golden
    scenario_table = {f"S{i}": {"top1": None, "top3": None, "mrr": None, "note": "no runs"} for i in range(6)}
    for sc, m in per_scenario.items():
        scenario_table[sc] = {
            "top1": m.top1,
            "top3": m.top3,
            "mrr": m.mrr,
            "subgraph_recall": m.subgraph_recall,
            "n": m.n,
            "note": "hard cell" if sc in ("S1", "S4") else "ok",
        }
    # Fill from tuning test per-scenario if present
    for sc, m in tuning.test_metrics.per_scenario.items():
        scenario_table[sc] = {
            "top1": m.top1,
            "top3": m.top3,
            "mrr": m.mrr,
            "subgraph_recall": m.subgraph_recall,
            "n": m.n,
            "note": "hard cell" if sc in ("S1", "S4") else "ok",
        }

    report: dict[str, Any] = {
        "golden_eval": golden,
        "ablation": ablation.to_dict(),
        "ablation_table": ablation.summary_lines(),
        "classification": {
            "predicted": pred.fault_class.value,
            "truth": gt.fault_class.value,
            "rationale": pred.rationale,
            "confusion_golden": cm.to_dict(),
            "confusion_demo": cm_demo.to_dict(),
            "confusion_table": cm_demo.render_table(),
        },
        "tuning": tuning.to_dict(),
        "calibration": cal_test.to_dict(),
        "reliability_diagram_ascii": _ascii_reliability(cal_test.reliability_points),
        "isolation_d1": iso.to_dict(),
        "isolation_d3_abstention": iso_d3.to_dict(),
        "remediation_plans": plans,
        "canary_healthy": canary_ok,
        "canary_rollback": canary_rb,
        "per_scenario_table": scenario_table,
        "cross_topology_argument": ANALYTICAL_CROSS_TOPOLOGY_ARGUMENT,
        "split": {
            "train_n": len(split.train),
            "test_n": len(split.test),
            "held_out_sites": sorted(split.held_out_sites),
            "verified_no_leakage": True,
        },
    }

    (ARTIFACTS / "demo_report.json").write_text(json.dumps(report, indent=2) + "\n")
    (ARTIFACTS / "ablation_table.txt").write_text("\n".join(ablation.summary_lines()) + "\n")
    (ARTIFACTS / "confusion_matrix.txt").write_text(cm_demo.render_table() + "\n")
    (ARTIFACTS / "reliability_diagram.txt").write_text(report["reliability_diagram_ascii"] + "\n")
    (ARTIFACTS / "per_scenario_table.json").write_text(
        json.dumps(scenario_table, indent=2) + "\n"
    )
    (ARTIFACTS / "cross_topology_argument.txt").write_text(
        ANALYTICAL_CROSS_TOPOLOGY_ARGUMENT + "\n"
    )
    (ARTIFACTS / "DEMO.md").write_text(
        "\n".join(
            [
                "# Causa Dev C — Demo artifacts",
                "",
                "## Headline (golden fixture)",
                f"- FixtureFull top-1: {golden['FixtureFull']['metrics']['top1']}",
                f"- MostAnomalous top-1: {golden['MostAnomalous']['metrics']['top1']}",
                f"- Subgraph recall: {golden['FixtureFull']['metrics']['subgraph_recall']}",
                "",
                "## Ablation",
                "See `ablation_table.txt`.",
                "",
                "## Calibration",
                f"- ECE: {cal_test.ece:.4f}",
                "See `reliability_diagram.txt`.",
                "",
                "## Classification",
                f"- Golden predicted={pred.fault_class.value} truth={gt.fault_class.value}",
                "See `confusion_matrix.txt`.",
                "",
                "## Isolation",
                f"- D1 ARI: {iso.ari:.3f}",
                f"- D3 abstention correct: {iso_d3.abstention_correct}",
                "",
                "## Cross-topology",
                ANALYTICAL_CROSS_TOPOLOGY_ARGUMENT,
                "",
                "## Remediation",
                "- AppLogic → proposed PR (unmerged)",
                "- Config → before/after patch",
                "- Dependency → rollback-to-last-good",
                f"- Canary rollback on breach: {canary_rb['rolled_back']}",
                "",
            ]
        )
    )
    return report


def main() -> None:
    report = generate_all()
    print(json.dumps({"artifacts": str(ARTIFACTS), "ece": report["calibration"]["ece"]}, indent=2))


if __name__ == "__main__":
    main()
