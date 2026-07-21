"""C4 adapter: Dev C Weights/EnabledSignals → Dev B scoring.Scorer."""

from __future__ import annotations

from typing import Any, Iterable

from adapters.subgraph import incident_subgraph_from_golden, resolve_incident_id
from contracts import EnabledSignals, RankedResult, Weights, normalize_enabled, normalize_weights
from graphs.incident_subgraph import IncidentSubgraph
from scoring.scorer import Scorer as KernelScorer


class ABScorer:
    """Implements the C4 Scorer Protocol by delegating to scoring.scorer.Scorer."""

    def __init__(self, inner: KernelScorer | None = None):
        self._inner = inner or KernelScorer()

    def score(
        self,
        incident_subgraph: Any,
        weights: Weights | dict[str, float] | None = None,
        enabled_signals: EnabledSignals | Iterable[str] | None = None,
    ) -> RankedResult:
        w = normalize_weights(weights)
        en = normalize_enabled(enabled_signals)
        if isinstance(incident_subgraph, IncidentSubgraph):
            sub = incident_subgraph
        else:
            sub = incident_subgraph_from_golden(
                incident_subgraph if isinstance(incident_subgraph, dict) else None
            )
        return self._inner.score(
            sub,
            weights=w.to_signal_dict(),
            enabled_signals=en.to_set(),
            incident_id=resolve_incident_id(incident_subgraph),
            anomaly=sub.anomaly,
        )
