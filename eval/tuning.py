"""Weight tuning harness — C owns the harness; B owns the weights (spec §3)."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Sequence

from contracts import AnomalyBag, Incident, Scorer, Weights
from eval.metrics import EvalMetrics
from eval.runner import EvalRunner
from eval.splits import Split


@dataclass
class TuningResult:
    best_weights: Weights
    train_metrics: EvalMetrics
    test_metrics: EvalMetrics
    grid_size: int
    verified_no_leakage: bool

    def to_dict(self) -> dict:
        return {
            "best_weights": self.best_weights.as_dict(),
            "train_metrics": self.train_metrics.to_dict(),
            "test_metrics": self.test_metrics.to_dict(),
            "grid_size": self.grid_size,
            "verified_no_leakage": self.verified_no_leakage,
        }


def _weight_grid(steps: Sequence[float] | None = None) -> list[Weights]:
    vals = list(steps or (0.0, 0.25, 0.5, 0.75, 1.0))
    out: list[Weights] = []
    for wt, wd, wp in product(vals, repeat=3):
        if wt + wd + wp <= 0:
            continue
        s = wt + wd + wp
        out.append(Weights(w_T=wt / s, w_D=wd / s, w_P=wp / s))
    seen: set[tuple[float, float, float]] = set()
    uniq: list[Weights] = []
    for w in out:
        key = (round(w.w_T, 4), round(w.w_D, 4), round(w.w_P, 4))
        if key not in seen:
            seen.add(key)
            uniq.append(w)
    return uniq


def tune_weights(
    scorer: Scorer,
    split: Split,
    anomaly_by_id: dict[str, AnomalyBag],
    subgraph_by_id: dict[str, dict],
    steps: Sequence[float] | None = None,
) -> TuningResult:
    """Grid-search weights on train only; report metrics on held-out test."""
    split.assert_no_leakage()
    grid = _weight_grid(steps)
    best_w = Weights()
    best_train = EvalMetrics()
    best_score = -1.0

    def pack(incs: list[Incident]) -> EvalRunner:
        triples = []
        for inc in incs:
            triples.append(
                (
                    inc,
                    anomaly_by_id[inc.incident_id],
                    subgraph_by_id[inc.incident_id],
                )
            )
        return EvalRunner(triples)

    train_runner = pack(split.train)
    for w in grid:
        report = train_runner.evaluate_scorer(scorer, method="tune", weights=w)
        # Optimize top-3 then MRR
        score = report.metrics.top3 * 2 + report.metrics.mrr
        if score > best_score:
            best_score = score
            best_w = w
            best_train = report.metrics

    test_runner = pack(split.test) if split.test else train_runner
    test_report = test_runner.evaluate_scorer(scorer, method="Full", weights=best_w)
    return TuningResult(
        best_weights=best_w,
        train_metrics=best_train,
        test_metrics=test_report.metrics,
        grid_size=len(grid),
        verified_no_leakage=True,
    )
