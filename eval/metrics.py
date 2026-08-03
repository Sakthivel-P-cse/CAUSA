"""Ranking accuracy metrics (spec §3 / §5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass
class EvalMetrics:
    top1: float = 0.0
    top3: float = 0.0
    top5: float = 0.0
    mrr: float = 0.0
    subgraph_recall: float = 0.0
    n: int = 0
    hits_at_1: int = 0
    hits_at_3: int = 0
    hits_at_5: int = 0
    recall_hits: int = 0
    per_scenario: dict[str, EvalMetrics] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "top1": self.top1,
            "top3": self.top3,
            "top5": self.top5,
            "mrr": self.mrr,
            "subgraph_recall": self.subgraph_recall,
            "hits_at_1": self.hits_at_1,
            "hits_at_3": self.hits_at_3,
            "hits_at_5": self.hits_at_5,
            "recall_hits": self.recall_hits,
            "per_scenario": {k: v.to_dict() for k, v in self.per_scenario.items()},
        }


def rank_of(true_root: str, ranked: Sequence[str]) -> int | None:
    try:
        return ranked.index(true_root) + 1  # 1-based
    except ValueError:
        return None


def subgraph_recall(true_root: str, subgraph_nodes: Iterable[str]) -> bool:
    return true_root in set(subgraph_nodes)


def reciprocal_rank(true_root: str, ranked: Sequence[str]) -> float:
    r = rank_of(true_root, ranked)
    return 0.0 if r is None else 1.0 / r


def hit_at_k(true_root: str, ranked: Sequence[str], k: int) -> bool:
    r = rank_of(true_root, ranked)
    return r is not None and r <= k


@dataclass
class _Accum:
    n: int = 0
    h1: int = 0
    h3: int = 0
    h5: int = 0
    rr_sum: float = 0.0
    recall: int = 0


def compute_metrics(
    cases: Sequence[tuple[str, Sequence[str], Sequence[str], str | None]],
) -> EvalMetrics:
    """cases: (true_root, ranked_ids, subgraph_ids, scenario_or_None)."""
    overall = _Accum()
    by_sc: dict[str, _Accum] = {}

    for true_root, ranked, subgraph, scenario in cases:
        overall.n += 1
        if subgraph_recall(true_root, subgraph):
            overall.recall += 1
        if hit_at_k(true_root, ranked, 1):
            overall.h1 += 1
        if hit_at_k(true_root, ranked, 3):
            overall.h3 += 1
        if hit_at_k(true_root, ranked, 5):
            overall.h5 += 1
        overall.rr_sum += reciprocal_rank(true_root, ranked)

        if scenario:
            acc = by_sc.setdefault(scenario, _Accum())
            acc.n += 1
            if subgraph_recall(true_root, subgraph):
                acc.recall += 1
            if hit_at_k(true_root, ranked, 1):
                acc.h1 += 1
            if hit_at_k(true_root, ranked, 3):
                acc.h3 += 1
            if hit_at_k(true_root, ranked, 5):
                acc.h5 += 1
            acc.rr_sum += reciprocal_rank(true_root, ranked)

    def finalize(a: _Accum) -> EvalMetrics:
        n = max(a.n, 1)
        return EvalMetrics(
            n=a.n,
            top1=a.h1 / n if a.n else 0.0,
            top3=a.h3 / n if a.n else 0.0,
            top5=a.h5 / n if a.n else 0.0,
            mrr=a.rr_sum / n if a.n else 0.0,
            subgraph_recall=a.recall / n if a.n else 0.0,
            hits_at_1=a.h1,
            hits_at_3=a.h3,
            hits_at_5=a.h5,
            recall_hits=a.recall,
        )

    result = finalize(overall)
    result.per_scenario = {k: finalize(v) for k, v in by_sc.items()}
    return result
