"""Baselines that do not need Dev B's Scorer (spec §3)."""

from __future__ import annotations

import random
from typing import Sequence

from contracts import (
    AnomalyBag,
    NodeKind,
    RankedResult,
    Role,
    RootCauseCandidate,
    SignalScores,
)


def _as_candidates(
    node_ids: Sequence[str],
    anomaly: AnomalyBag | None,
) -> list[RootCauseCandidate]:
    out: list[RootCauseCandidate] = []
    for i, nid in enumerate(node_ids):
        score = anomaly.score(nid) if anomaly else float(len(node_ids) - i)
        dev = anomaly.dev_vector(nid) if anomaly else {}
        out.append(
            RootCauseCandidate(
                node_id=nid,
                node_kind=NodeKind.SERVICE,
                score=score,
                confidence=0.0,
                role=Role.ORIGIN if i == 0 else Role.VICTIM,
                signals=SignalScores(),
                dev_vector=dev,
                explanation=f"baseline candidate rank={i + 1}",
            )
        )
    return out


def random_rank(
    incident_id: str,
    node_ids: Sequence[str],
    anomaly: AnomalyBag | None = None,
    seed: int = 0,
) -> RankedResult:
    nodes = list(node_ids)
    rng = random.Random(seed)
    rng.shuffle(nodes)
    return RankedResult(
        incident_id=incident_id,
        candidates=_as_candidates(nodes, anomaly),
    )


def most_anomalous_rank(
    incident_id: str,
    node_ids: Sequence[str],
    anomaly: AnomalyBag,
) -> RankedResult:
    ranked = sorted(node_ids, key=lambda n: anomaly.score(n), reverse=True)
    return RankedResult(
        incident_id=incident_id,
        candidates=_as_candidates(ranked, anomaly),
    )
