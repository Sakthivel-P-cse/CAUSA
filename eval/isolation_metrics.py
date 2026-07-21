"""Multi-fault isolation metrics (spec §7).

Consumes IsolationView — not Dev B's isolation package — so eval stays decoupled.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

from adapters.isolation import IsolationView
from contracts import DualInjectionGroundTruth


@dataclass
class IsolationMetrics:
    cluster_count_accuracy: float
    per_fault_top1: float
    per_fault_top3: float
    ari: float
    abstention_correct: float | None
    n: int

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "cluster_count_accuracy": self.cluster_count_accuracy,
            "per_fault_top1": self.per_fault_top1,
            "per_fault_top3": self.per_fault_top3,
            "ari": self.ari,
            "abstention_correct": self.abstention_correct,
        }


def adjusted_rand_index(y_true: Mapping[str, int], y_pred: Mapping[str, int]) -> float:
    nodes = sorted(set(y_true) & set(y_pred))
    if len(nodes) < 2:
        return 1.0 if len(nodes) <= 1 else 0.0

    a = [y_true[n] for n in nodes]
    b = [y_pred[n] for n in nodes]
    n = len(nodes)

    def comb2(x: int) -> float:
        return x * (x - 1) / 2.0

    pairs = Counter(zip(a, b))
    rows = Counter(a)
    cols = Counter(b)

    sum_comb_c = sum(comb2(v) for v in pairs.values())
    sum_comb_rows = sum(comb2(v) for v in rows.values())
    sum_comb_cols = sum(comb2(v) for v in cols.values())
    comb_n = comb2(n)
    if comb_n == 0:
        return 1.0
    expected = sum_comb_rows * sum_comb_cols / comb_n
    max_index = 0.5 * (sum_comb_rows + sum_comb_cols)
    denom = max_index - expected
    if denom == 0:
        return 1.0
    return (sum_comb_c - expected) / denom


def per_fault_topk(gt: DualInjectionGroundTruth, view: IsolationView, k: int) -> float:
    if view.abstained or not view.clusters:
        return 0.0
    hits = 0
    used: set[int] = set()
    ranked_lists = view.ranked_lists()
    for root in gt.true_root_nodes:
        for i, ranked in enumerate(ranked_lists):
            if i in used:
                continue
            if root in ranked[:k]:
                hits += 1
                used.add(i)
                break
    return hits / max(len(gt.true_root_nodes), 1)


def evaluate_isolation(
    cases: Sequence[tuple[DualInjectionGroundTruth, IsolationView]],
) -> IsolationMetrics:
    n = len(cases)
    if n == 0:
        return IsolationMetrics(0, 0, 0, 0, None, 0)

    cc_hits = 0
    top1_sum = 0.0
    top3_sum = 0.0
    ari_sum = 0.0
    abst_cases = 0
    abst_correct = 0

    for gt, view in cases:
        if gt.expect_abstention:
            abst_cases += 1
            if view.abstained:
                abst_correct += 1
        expected_k = gt.expected_cluster_count()
        predicted_k = view.cluster_count
        if (gt.expect_abstention and view.abstained) or (
            not gt.expect_abstention and predicted_k == expected_k
        ):
            cc_hits += 1

        if not view.abstained:
            top1_sum += per_fault_topk(gt, view, 1)
            top3_sum += per_fault_topk(gt, view, 3)
            ari_sum += adjusted_rand_index(gt.node_to_fault, view.predicted_partition())
        elif gt.expect_abstention:
            ari_sum += 1.0
            top1_sum += 1.0
            top3_sum += 1.0

    return IsolationMetrics(
        cluster_count_accuracy=cc_hits / n,
        per_fault_top1=top1_sum / n,
        per_fault_top3=top3_sum / n,
        ari=ari_sum / n,
        abstention_correct=(abst_correct / abst_cases) if abst_cases else None,
        n=n,
    )


def pairwise_agreement_demo() -> float:
    nodes = ["a", "b", "c", "d"]
    true = {n: i // 2 for i, n in enumerate(nodes)}
    return adjusted_rand_index(true, dict(true))
