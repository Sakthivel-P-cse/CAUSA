"""Fixture-backed Scorer stand-in until Dev B's Scorer is wired.

Implements the C4 port so ablation / tuning / calibration run without importing
scoring internals. Reweights fixture T/D/P only — no RCA math reimplementation.
"""

from __future__ import annotations

from typing import Any, Iterable

from contracts import (
    EnabledSignals,
    RankedResult,
    RootCauseCandidate,
    SignalScores,
    Weights,
    normalize_enabled,
    normalize_weights,
)
from fixtures import GOLDEN_INCIDENT_ID, load_ranked_result


class FixtureScorer:
    """Re-rank golden candidates by weighted T/D/P for harness development."""

    def __init__(self, base: RankedResult | None = None, expected_incident_id: str | None = None):
        self.base = base or load_ranked_result()
        self.expected_incident_id = expected_incident_id or self.base.incident_id or GOLDEN_INCIDENT_ID

    def score(
        self,
        incident_subgraph: Any,
        weights: Weights | dict[str, float] | None = None,
        enabled_signals: EnabledSignals | Iterable[str] | None = None,
    ) -> RankedResult:
        incident_id = None
        if isinstance(incident_subgraph, dict):
            incident_id = incident_subgraph.get("incident_id")
        if incident_id and incident_id != self.expected_incident_id:
            raise ValueError(
                f"FixtureScorer refuse silent reuse: got incident_id={incident_id!r}, "
                f"expected {self.expected_incident_id!r}"
            )

        w = normalize_weights(weights)
        en = normalize_enabled(enabled_signals)
        rescored: list[RootCauseCandidate] = []
        for c in self.base.candidates:
            t = c.signals.T if en.T else 0.0
            d = c.signals.D if en.D else 0.0
            p = c.signals.P if en.P else 0.0
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
                    cluster_id=c.cluster_id,
                    flags=dict(c.flags),
                )
            )
        rescored.sort(key=lambda x: x.score, reverse=True)
        return RankedResult(
            incident_id=self.base.incident_id,
            candidates=rescored,
            fault_class=self.base.fault_class,
            abstain_reason=self.base.abstain_reason,
        )
