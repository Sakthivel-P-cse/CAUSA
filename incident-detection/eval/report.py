"""Fixture smoke artifacts — golden incident only (not a portfolio claim)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from adapters.logs import messages_from_fixture_logs
from classify.classifier import FaultClassifier
from classify.confusion import score_classification
from contracts import FaultClass
from eval.ablation import run_ablation
from eval.calibration import apply_calibrated_confidence, fit_calibration, raw_confidence
from eval.fixture_scorer import FixtureScorer
from eval.runner import run_golden_fixture
from eval.splits import ANALYTICAL_CROSS_TOPOLOGY_ARGUMENT
from fixtures import load_ground_truth, load_logs, load_ranked_result, load_subgraph
from remediation.canary import SimulatedCanary
from remediation.router import RemediationRouter


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def _ascii_reliability(points: list[tuple[float, float]]) -> str:
    lines = ["predicted\tempirical", "---------\t---------"]
    for p, e in points:
        bar_p = int(round(p * 20))
        bar_e = int(round(e * 20))
        lines.append(f"{p:.2f} {'#' * bar_p}\t{e:.2f} {'*' * bar_e}")
    return "\n".join(lines)


def generate_all() -> dict[str, Any]:
    """Generate smoke artifacts from the golden fixture only (n=1)."""
    ARTIFACTS.mkdir(exist_ok=True)
    golden = run_golden_fixture()

    from eval.runner import EvalRunner

    runner = EvalRunner.from_golden()
    scorer = FixtureScorer()
    ablation = run_ablation(runner, scorer)
    try:
        from adapters.scorer import ABScorer

        ab_ablation = run_ablation(runner, ABScorer())
    except Exception as exc:  # pragma: no cover
        ab_ablation = None
        ab_ablation_error = str(exc)
    else:
        ab_ablation_error = None

    ranked = load_ranked_result()
    logs_by_node = messages_from_fixture_logs(load_logs())
    clf = FaultClassifier(logs_by_node)
    top = ranked.candidates[0]
    pred = clf.classify(top)
    gt = load_ground_truth()
    cm = score_classification([(gt.fault_class, pred.fault_class)])

    # Calibration fit on the single golden result (smoke only — ECE not a claim)
    fitted = fit_calibration([(ranked, gt.true_root_node)])
    calibrated = apply_calibrated_confidence(
        scorer.score(load_subgraph()),
        fitted,
    )

    router = RemediationRouter()
    plans = {
        "Dependency": router.route(FaultClass.DEPENDENCY, top, ranked).to_dict(),
        "AppLogic": router.route(FaultClass.APP_LOGIC, top, ranked).to_dict(),
        "Config": router.route(FaultClass.CONFIG, top, ranked).to_dict(),
    }
    canary_ok = SimulatedCanary().apply().to_dict()
    canary_rb = SimulatedCanary(metric_fn=SimulatedCanary.breaching_metrics).apply().to_dict()

    per_scenario = ablation.rows["Full"].metrics.per_scenario
    scenario_table = {
        f"S{i}": {"top1": None, "top3": None, "mrr": None, "note": "no runs"}
        for i in range(6)
    }
    for sc, m in per_scenario.items():
        scenario_table[sc] = {
            "top1": m.top1,
            "top3": m.top3,
            "mrr": m.mrr,
            "subgraph_recall": m.subgraph_recall,
            "n": m.n,
            "note": "fixture smoke",
        }

    report: dict[str, Any] = {
        "kind": "fixture_smoke",
        "disclaimer": (
            "Artifacts from golden fixture n=1 only (product-catalog / golden-0001). "
            "Not a multi-incident S0–S5 portfolio claim — that is M3 after chaos corpus."
        ),
        "golden_eval": golden,
        "ablation": ablation.to_dict(),
        "ablation_table": ablation.summary_lines(),
        "ab_ablation": ab_ablation.to_dict() if ab_ablation is not None else None,
        "ab_ablation_error": ab_ablation_error,
        "classification": {
            "predicted": pred.fault_class.value,
            "truth": gt.fault_class_value(),
            "rationale": pred.rationale,
            "confusion_golden": cm.to_dict(),
            "confusion_table": cm.render_table(),
        },
        "calibration_smoke": {
            "ece": fitted.ece,
            "raw_confidence": raw_confidence(calibrated.candidates),
            "calibrated_top_confidence": calibrated.candidates[0].confidence,
            "reliability_diagram_ascii": _ascii_reliability(fitted.reliability_points),
        },
        "remediation_plans": plans,
        "canary_healthy": canary_ok,
        "canary_rollback": canary_rb,
        "per_scenario_table": scenario_table,
        "cross_topology_argument": ANALYTICAL_CROSS_TOPOLOGY_ARGUMENT,
    }

    (ARTIFACTS / "demo_report.json").write_text(json.dumps(report, indent=2) + "\n")
    (ARTIFACTS / "ablation_table.txt").write_text("\n".join(ablation.summary_lines()) + "\n")
    (ARTIFACTS / "confusion_matrix.txt").write_text(cm.render_table() + "\n")
    (ARTIFACTS / "reliability_diagram.txt").write_text(
        report["calibration_smoke"]["reliability_diagram_ascii"] + "\n"
    )
    (ARTIFACTS / "per_scenario_table.json").write_text(
        json.dumps(scenario_table, indent=2) + "\n"
    )
    (ARTIFACTS / "cross_topology_argument.txt").write_text(
        ANALYTICAL_CROSS_TOPOLOGY_ARGUMENT + "\n"
    )
    (ARTIFACTS / "DEMO.md").write_text(
        "\n".join(
            [
                "# Causa Dev C — Fixture smoke artifacts",
                "",
                "> n=1 golden fixture only. Not a multi-incident headline claim.",
                "",
                "## Golden fixture smoke",
                f"- FixtureFull top-1: {golden['FixtureFull']['metrics']['top1']}",
                f"- MostAnomalous top-1: {golden['MostAnomalous']['metrics']['top1']}",
                f"- Subgraph recall: {golden['FixtureFull']['metrics']['subgraph_recall']}",
                "",
                "## Ablation",
                "See `ablation_table.txt` (FixtureScorer reweight of golden signals).",
                "",
                "## Classification",
                f"- Predicted={pred.fault_class.value} truth={gt.fault_class_value()}",
                "See `confusion_matrix.txt`.",
                "",
                "## Remediation (proposed payloads only)",
                "- AppLogic → proposed PR payload (never auto-merged)",
                "- Config → before/after patch proposal",
                "- Dependency → rollback-to-last-good proposal",
                f"- Canary rollback on breach: {canary_rb['rolled_back']}",
                "",
            ]
        )
        + "\n"
    )
    return report


def main() -> None:
    report = generate_all()
    print(
        json.dumps(
            {
                "artifacts": str(ARTIFACTS),
                "kind": report["kind"],
                "ece_smoke": report["calibration_smoke"]["ece"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
