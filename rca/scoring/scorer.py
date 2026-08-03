"""C4 Scorer interface — pure scoring for ablation / weight sweeps.

See also ``docs/scorer_c4.md`` for Dev C harness integration notes.

Call::

    from scoring.scorer import score, Scorer, DEFAULT_WEIGHTS

    result = score(
        subgraph,
        weights={"T": 0.5, "D": 0.3, "P": 0.2},  # default 1/3 each
        enabled_signals={"T", "D", "P"},          # disabled → zeroed internally
    )

Disabled signals contribute **0** regardless of the weight dict — the scorer
zeros them so ablation harnesses can leave weights unchanged while dropping
signals. Deterministic and side-effect free given the same inputs.
"""

from __future__ import annotations

from typing import Iterable

from contracts import RankedResult
from graphs.incident_subgraph import IncidentSubgraph
from graphs.influence_graph import InfluenceGraph, build_influence_graph
from rca.types import KernelAnomaly

from .arbitration import arbitrate
from .fault_class import derive_fault_class
from .fitted_weights import load_fitted_weights
from .output import (
    build_candidates,
    resolve_origins,
    stamp_confidence,
)
from .signals import compute_signals


def _default_weight_dict() -> dict[str, float]:
    w = load_fitted_weights()
    return {"T": w.w_T, "D": w.w_D, "P": w.w_P}


def default_weights() -> dict[str, float]:
    """Call-time defaults from fit artifact (equal thirds when missing/rejected)."""
    return _default_weight_dict()


# Boot snapshot for importers; ``score()`` always reloads via ``default_weights()``.
DEFAULT_WEIGHTS: dict[str, float] = {"T": 1.0 / 3.0, "D": 1.0 / 3.0, "P": 1.0 / 3.0}
ALL_SIGNALS = frozenset({"T", "D", "P"})


def score(
    subgraph: IncidentSubgraph,
    weights: dict[str, float] | None = None,
    enabled_signals: Iterable[str] | None = None,
    *,
    ig: InfluenceGraph | None = None,
    anomaly: KernelAnomaly | None = None,
    top_c: int = 5,
    run_arbitration: bool = True,
    incident_id: str = "",
    seed_ids: set[str] | None = None,
    horizon: float | None = None,
    alerted_service: str | None = None,
) -> RankedResult:
    """Deterministic RCA scoring over an incident subgraph (C4).

    Parameters
    ----------
    subgraph:
        Incident subgraph (nodes/edges + anomaly annotation).
    weights:
        ``{"T": float, "D": float, "P": float}``. Defaults to equal 1/3.
    enabled_signals:
        Subset of ``{"T", "D", "P"}``. Disabled signals contribute **0** —
        this function zeros them internally; caller weights for disabled
        keys are ignored.
    ig:
        Optional pre-built Influence Graph. If omitted, built once from
        ``subgraph`` and reused for all signals + arbitration.
    anomaly:
        Defaults to ``subgraph.anomaly``.
    top_c:
        Top-C window for layer arbitration.
    run_arbitration:
        When True, apply infra-vs-service re-ranking pass.
    incident_id:
        Stamped onto ``RankedResult.incident_id`` for Dev C eval join.

    Returns
    -------
    RankedResult with candidates sorted by score descending (deterministic).
    Each candidate carries ``node_kind``, nested ``signals``, ``confidence``
    (raw §5 formula; calibration is Dev C), and ``dev_vector``.

    Notes
    -----
    Side-effect free and deterministic given the same inputs — safe to call
    repeatedly with varying weights / signal subsets for ablation sweeps.
    No tuning logic lives here.
    """
    weights = dict(weights if weights is not None else _default_weight_dict())
    enabled = set(enabled_signals) if enabled_signals is not None else set(ALL_SIGNALS)
    enabled &= set(ALL_SIGNALS)

    # Zero disabled signals internally.
    effective = {k: (weights.get(k, 0.0) if k in enabled else 0.0) for k in ALL_SIGNALS}

    anomaly = anomaly or subgraph.anomaly
    if anomaly is None:
        raise ValueError("anomaly required (pass anomaly= or set subgraph.anomaly)")

    # Build IG once; never rebuild per signal.
    if ig is None:
        ig = build_influence_graph(subgraph)

    # Normalize time over direct-evidence nodes, not the permissively expanded
    # membership — see IncidentSubgraph.seed_ids. Explicit `seed_ids` wins so
    # isolation can pass a cluster-local basis.
    basis = seed_ids if seed_ids is not None else (subgraph.seed_ids or None)
    signals = compute_signals(ig, anomaly, seed_ids=basis, horizon=horizon)

    raw_scores: dict[str, float] = {}
    for nid, sig in signals.by_node.items():
        s = (
            effective["T"] * sig.T
            + effective["D"] * sig.D
            + effective["P"] * sig.P
        )
        raw_scores[nid] = s

    candidates = build_candidates(signals, raw_scores, anomaly, ig)
    if run_arbitration:
        candidates = arbitrate(
            candidates,
            ig,
            anomaly,
            top_c=top_c,
            alerted_service=alerted_service,
        )
        # Re-stamp confidence after possible score swaps / reordering.
        stamp_confidence(candidates)

    # ORIGIN is a claim about the incident, so it can only be settled once the
    # ranking is final — including any arbitration reorder above.
    resolve_origins(candidates)

    return RankedResult(
        candidates=candidates,
        incident_id=incident_id,
        # Structural floor only — None when the kernel can't justify a class.
        # Full classification: classify.classifier / remediation NIM merge.
        fault_class=derive_fault_class(candidates, ig),
    )


class Scorer:
    """Thin callable wrapper around :func:`score` (C4 seam).

    Dev C's tuning / ablation harness should call this (or ``score``) with
    arbitrary ``weights`` and ``enabled_signals`` — never reimplement T/D/P.
    """

    def score(
        self,
        subgraph: IncidentSubgraph,
        weights: dict[str, float] | None = None,
        enabled_signals: Iterable[str] | None = None,
        **kwargs,
    ) -> RankedResult:
        return score(subgraph, weights, enabled_signals, **kwargs)
