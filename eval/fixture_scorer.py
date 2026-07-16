"""Fixture-backed Scorer stand-in until Dev B lands.

Implements C4 so ablation / tuning / calibration can run end-to-end.
Uses fixture RankedResult signal scores reweighted by enabled_signals.
"""

from __future__ import annotations

from typing import Any

from contracts.c3_candidates import (
    RankedCluster,
    RankedResult,
    RootCauseCandidate,
    SignalScores,
)
from contracts.c4_scorer import EnabledSignals, Weights
from fixtures import load_ranked_result


class FixtureScorer:
    """Re-rank golden candidates by weighted T/D/P for harness development."""

    def __init__(self, base: RankedResult | None = None):
        self.base = base or load_ranked_result()

    def score(
        self,
        incident_subgraph: Any,
        weights: Weights | None = None,
        enabled_signals: EnabledSignals | None = None,
    ) -> RankedResult:
        w = weights or Weights()
        en = enabled_signals or EnabledSignals.all()
        clusters: list[RankedCluster] = []
        for cl in self.base.clusters:
            rescored: list[RootCauseCandidate] = []
            for c in cl.candidates:
                t = c.signals.T if en.T else 0.0
                d = c.signals.D if en.D else 0.0
                p = c.signals.P if en.P else 0.0
                # Renormalize active weights
                active = []
                if en.T:
                    active.append(w.w_T)
                if en.D:
                    active.append(w.w_D)
                if en.P:
                    active.append(w.w_P)
                denom = sum(active) or 1.0
                wt = (w.w_T / denom) if en.T else 0.0
                wd = (w.w_D / denom) if en.D else 0.0
                wp = (w.w_P / denom) if en.P else 0.0
                score = wt * t + wd * d + wp * p
                rescored.append(
                    RootCauseCandidate(
                        node_id=c.node_id,
                        node_kind=c.node_kind,
                        score=score,
                        confidence=c.confidence,
                        role=c.role,
                        signals=SignalScores(T=t, D=d, P=p),
                        dev_vector=dict(c.dev_vector),
                        explanation=c.explanation,
                    )
                )
            rescored.sort(key=lambda x: x.score, reverse=True)
            clusters.append(
                RankedCluster(
                    fault_id=cl.fault_id,
                    candidates=rescored,
                    node_ids=list(cl.node_ids) or [c.node_id for c in rescored],
                )
            )
        return RankedResult(
            incident_id=self.base.incident_id,
            clusters=clusters,
            fault_class=self.base.fault_class,
            abstained=self.base.abstained,
            abstention_reason=self.base.abstention_reason,
        )
