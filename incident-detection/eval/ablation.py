"""Ablation table + per-signal baselines via C4 Scorer (spec §3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts import EnabledSignals, Scorer, Weights
from eval.runner import EvalReport, EvalRunner


@dataclass
class AblationTable:
    rows: dict[str, EvalReport]

    def to_dict(self) -> dict[str, Any]:
        return {name: r.to_dict() for name, r in self.rows.items()}

    def summary_lines(self) -> list[str]:
        lines = ["method\ttop1\ttop3\tmrr\tsubgraph_recall"]
        for name, report in self.rows.items():
            m = report.metrics
            lines.append(
                f"{name}\t{m.top1:.3f}\t{m.top3:.3f}\t{m.mrr:.3f}\t{m.subgraph_recall:.3f}"
            )
        return lines


def run_ablation(
    runner: EvalRunner,
    scorer: Scorer,
    weights: Weights | None = None,
) -> AblationTable:
    """Drop-one + single-signal + Full, all through the real Scorer interface."""
    configs: list[tuple[str, EnabledSignals]] = [
        ("Full", EnabledSignals.all()),
        ("EarliestOnly", EnabledSignals.only("T")),
        ("CascadeOnly", EnabledSignals.only("D")),
        ("PropagationOnly", EnabledSignals.only("P")),
        ("NoT", EnabledSignals.without("T")),
        ("NoD", EnabledSignals.without("D")),
        ("NoP", EnabledSignals.without("P")),
    ]
    rows: dict[str, EvalReport] = {}
    for name, enabled in configs:
        rows[name] = runner.evaluate_scorer(
            scorer, method=name, weights=weights, enabled_signals=enabled
        )
    # Cheap baselines without scorer
    rows["Random"] = runner.evaluate_random(seed=0)
    rows["MostAnomalous"] = runner.evaluate_most_anomalous()
    return AblationTable(rows=rows)
