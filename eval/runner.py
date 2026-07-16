"""Eval runner: candidates vs GroundTruth → metrics.

Works against fixtures before Dev B's scorer exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from contracts.c2_anomaly import AnomalyOutput
from contracts.c3_candidates import RankedResult
from contracts.c4_scorer import EnabledSignals, Scorer, Weights
from contracts.c5_ground_truth import Incident
from eval.baselines import most_anomalous_rank, random_rank
from eval.metrics import EvalMetrics, compute_metrics
from fixtures import (
    load_anomaly,
    load_ground_truth,
    load_ranked_result,
    load_subgraph,
)


RankProvider = Callable[[Incident, AnomalyOutput, dict], RankedResult]


@dataclass
class EvalCase:
    incident: Incident
    anomaly: AnomalyOutput
    subgraph: dict
    result: RankedResult
    method: str


@dataclass
class EvalReport:
    method: str
    metrics: EvalMetrics
    cases: list[EvalCase] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"method": self.method, "metrics": self.metrics.to_dict()}


class EvalRunner:
    def __init__(self, incidents: list[tuple[Incident, AnomalyOutput, dict]] | None = None):
        self.incidents = incidents or []

    @classmethod
    def from_golden(cls) -> EvalRunner:
        gt = load_ground_truth()
        anomaly = load_anomaly()
        subgraph = load_subgraph()
        return cls([(gt, anomaly, subgraph)])

    def evaluate(self, method: str, provider: RankProvider) -> EvalReport:
        cases_out: list[EvalCase] = []
        metric_cases: list[tuple[str, list[str], list[str], str | None]] = []

        for incident, anomaly, subgraph in self.incidents:
            result = provider(incident, anomaly, subgraph)
            ranked = result.ranked_node_ids(0)
            subgraph_ids = incident.subgraph_node_ids or [n["id"] for n in subgraph.get("nodes", [])]
            scenario = incident.scenario.value if incident.scenario else None
            metric_cases.append((incident.true_root_node, ranked, subgraph_ids, scenario))
            cases_out.append(
                EvalCase(
                    incident=incident,
                    anomaly=anomaly,
                    subgraph=subgraph,
                    result=result,
                    method=method,
                )
            )

        return EvalReport(method=method, metrics=compute_metrics(metric_cases), cases=cases_out)

    def evaluate_random(self, seed: int = 0) -> EvalReport:
        def provider(inc: Incident, anomaly: AnomalyOutput, subgraph: dict) -> RankedResult:
            nodes = inc.subgraph_node_ids or [n["id"] for n in subgraph.get("nodes", [])]
            return random_rank(inc.incident_id, nodes, anomaly, seed=seed)

        return self.evaluate("Random", provider)

    def evaluate_most_anomalous(self) -> EvalReport:
        def provider(inc: Incident, anomaly: AnomalyOutput, subgraph: dict) -> RankedResult:
            nodes = inc.subgraph_node_ids or [n["id"] for n in subgraph.get("nodes", [])]
            return most_anomalous_rank(inc.incident_id, nodes, anomaly)

        return self.evaluate("MostAnomalous", provider)

    def evaluate_fixture_ranked(self) -> EvalReport:
        """Use pre-authored RankedResult (golden fixture / Dev B dump)."""
        fixture_result = load_ranked_result()

        def provider(inc: Incident, anomaly: AnomalyOutput, subgraph: dict) -> RankedResult:
            if inc.incident_id == fixture_result.incident_id:
                return fixture_result
            return fixture_result

        return self.evaluate("FixtureFull", provider)

    def evaluate_scorer(
        self,
        scorer: Scorer,
        method: str = "Full",
        weights: Weights | None = None,
        enabled_signals: EnabledSignals | None = None,
    ) -> EvalReport:
        def provider(inc: Incident, anomaly: AnomalyOutput, subgraph: dict) -> RankedResult:
            return scorer.score(subgraph, weights=weights, enabled_signals=enabled_signals)

        return self.evaluate(method, provider)


def run_golden_fixture() -> dict[str, Any]:
    """CI entrypoint: baselines + fixture full ranking on golden incident."""
    runner = EvalRunner.from_golden()
    reports = {
        "Random": runner.evaluate_random(seed=42),
        "MostAnomalous": runner.evaluate_most_anomalous(),
        "FixtureFull": runner.evaluate_fixture_ranked(),
    }
    out = {name: r.to_dict() for name, r in reports.items()}

    # Integrity checks for CI gate
    full = reports["FixtureFull"].metrics
    ma = reports["MostAnomalous"].metrics
    assert full.n == 1, "golden must have exactly one incident"
    assert full.subgraph_recall == 1.0, "true root must be in golden subgraph"
    assert full.top1 == 1.0, "FixtureFull must rank true root at top-1"
    assert ma.top1 == 1.0, "MostAnomalous should also top-1 on this fixture"
    return out


def main() -> None:
    result = run_golden_fixture()
    print(json.dumps(result, indent=2))
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "golden_eval.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Wrote artifacts/golden_eval.json")


if __name__ == "__main__":
    main()
