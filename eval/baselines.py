"""Baselines that do not need Dev B's Scorer (spec §3)."""

from __future__ import annotations

import random
from typing import Sequence

from contracts.c2_anomaly import AnomalyOutput
from contracts.c3_candidates import (
    NodeKind,
    NodeRole,
    RankedCluster,
    RankedResult,
    RootCauseCandidate,
    SignalScores,
)


def _as_candidates(node_ids: Sequence[str], anomaly: AnomalyOutput | None) -> list[RootCauseCandidate]:
    out: list[RootCauseCandidate] = []
    for i, nid in enumerate(node_ids):
        score = anomaly.score(nid) if anomaly else float(len(node_ids) - i)
        dev = anomaly.nodes[nid].dev_vector if anomaly and nid in anomaly.nodes else {}
        out.append(
            RootCauseCandidate(
                node_id=nid,
                node_kind=NodeKind.SERVICE,
                score=score,
                confidence=0.0,
                role=NodeRole.ORIGIN if i == 0 else NodeRole.VICTIM,
                signals=SignalScores(),
                dev_vector=dev,
                explanation=f"baseline candidate rank={i + 1}",
            )
        )
    return out


def random_rank(
    incident_id: str,
    node_ids: Sequence[str],
    anomaly: AnomalyOutput | None = None,
    seed: int = 0,
) -> RankedResult:
    nodes = list(node_ids)
    rng = random.Random(seed)
    rng.shuffle(nodes)
    cands = _as_candidates(nodes, anomaly)
    return RankedResult(
        incident_id=incident_id,
        clusters=[RankedCluster(fault_id=None, candidates=cands, node_ids=list(nodes))],
    )


def most_anomalous_rank(
    incident_id: str,
    node_ids: Sequence[str],
    anomaly: AnomalyOutput,
) -> RankedResult:
    """Sort subgraph by anomaly_score descending — strong cheap baseline."""
    ranked = sorted(node_ids, key=lambda n: anomaly.score(n), reverse=True)
    cands = _as_candidates(ranked, anomaly)
    return RankedResult(
        incident_id=incident_id,
        clusters=[RankedCluster(fault_id=None, candidates=cands, node_ids=list(ranked))],
    )
