"""Multi-fault isolation metrics (spec §7): cluster-count, per-fault top-k, ARI, abstention."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Mapping, Sequence

from contracts.c3_candidates import RankedResult
from contracts.c5_ground_truth import DualInjectionGroundTruth


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
    """ARI over nodes present in both label maps."""
    nodes = sorted(set(y_true) & set(y_pred))
    if len(nodes) < 2:
        return 1.0 if len(nodes) <= 1 else 0.0

    def label_vec(m: Mapping[str, int]) -> list[int]:
        return [m[n] for n in nodes]

    a = label_vec(y_true)
    b = label_vec(y_pred)
    n = len(nodes)

    def comb2(x: int) -> float:
        return x * (x - 1) / 2.0

    # Contingency
    from collections import Counter

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


def predicted_partition(result: RankedResult) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for i, cl in enumerate(result.clusters):
        members = cl.node_ids or [c.node_id for c in cl.candidates]
        for nid in members:
            mapping[nid] = i
    return mapping


def per_fault_topk(
    gt: DualInjectionGroundTruth,
    result: RankedResult,
    k: int,
) -> float:
    """Each true root should top exactly one returned cluster (within top-k of that cluster)."""
    if result.abstained:
        return 0.0
    if not result.clusters:
        return 0.0
    hits = 0
    used_clusters: set[int] = set()
    for root in gt.true_root_nodes:
        found = False
        for i, cl in enumerate(result.clusters):
            if i in used_clusters:
                continue
            ranked = [c.node_id for c in cl.candidates]
            if root in ranked[:k]:
                hits += 1
                used_clusters.add(i)
                found = True
                break
        if not found:
            continue
    return hits / max(len(gt.true_root_nodes), 1)


def evaluate_isolation(
    cases: Sequence[tuple[DualInjectionGroundTruth, RankedResult]],
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

    for gt, result in cases:
        if gt.expect_abstention:
            abst_cases += 1
            if result.abstained:
                abst_correct += 1
            # Still count cluster metrics as 0/1 for transparency
        expected_k = gt.expected_cluster_count()
        predicted_k = 0 if result.abstained else len(result.clusters)
        if (gt.expect_abstention and result.abstained) or (
            not gt.expect_abstention and predicted_k == expected_k
        ):
            cc_hits += 1

        if not result.abstained:
            top1_sum += per_fault_topk(gt, result, 1)
            top3_sum += per_fault_topk(gt, result, 3)
            ari_sum += adjusted_rand_index(gt.node_to_fault, predicted_partition(result))
        elif gt.expect_abstention:
            # Abstaining correctly: treat partition quality as perfect for that case
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
    """Tiny self-check helper used in tests."""
    nodes = ["a", "b", "c", "d"]
    true = {n: i // 2 for i, n in enumerate(nodes)}
    pred = dict(true)
    return adjusted_rand_index(true, pred)
