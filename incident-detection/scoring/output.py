"""RootCauseCandidate emission + templated explanation sentences."""

from __future__ import annotations

import re

from contracts import (
    NodeKind,
    Role,
    RootCauseCandidate,
    SignalScores,
)
from graphs.influence_graph import InfluenceGraph
from rca.types import KernelAnomaly

from .fitted_weights import apply_calibration_map, load_calibration_map
from .signals import NodeSignals, SignalResult

EPS = 1e-9
def _fmt(x: float) -> str:
    return f"{x:.2f}"


def render_explanation(
    node_id: str,
    sig: NodeSignals,
    anomaly: KernelAnomaly,
    ig: InfluenceGraph,
) -> str:
    """Templated, deterministic explanation — no LLM."""
    fdt = anomaly.first_deviation_time.get(node_id)
    out_deg = ig.out_degree(node_id)
    in_deg = ig.in_degree(node_id)

    if sig.flags.get("slow_burn_T"):
        t_part = (
            f"{node_id} is a slow-burn / no-deviation node "
            f"(T={_fmt(sig.T)} neutral)"
        )
    elif fdt is not None:
        # Compare against mean caller/successor deviation when available.
        succ_times = [
            anomaly.first_deviation_time[s]
            for s in ig.successors(node_id)
            if anomaly.first_deviation_time.get(s) is not None
        ]
        if succ_times:
            lead = min(succ_times) - fdt
            t_part = (
                f"{node_id} deviated {_fmt(lead)}s before its influence targets "
                f"(T={_fmt(sig.T)})"
            )
        else:
            t_part = (
                f"{node_id} first deviation at t={_fmt(fdt)}s "
                f"(T={_fmt(sig.T)})"
            )
    else:
        t_part = f"{node_id} (T={_fmt(sig.T)})"

    if in_deg == 0 and out_deg > 0:
        d_part = (
            f"influence flows outward to {out_deg} node(s) with no incoming "
            f"influence edges (D={_fmt(sig.D)})"
        )
    else:
        d_part = (
            f"out-influence={_fmt(sig.out_influence)}, "
            f"in-influence={_fmt(sig.in_influence)} (D={_fmt(sig.D)})"
        )

    if sig.flags.get("zero_outdegree_P"):
        p_part = f"no outgoing influence edges (P={_fmt(sig.P)} flagged)"
    else:
        p_part = (
            f"{sig.p_consistent}/{sig.p_total} outgoing edges consistent "
            f"in order (P={_fmt(sig.P)})"
        )

    return (
        f"{t_part}, {d_part}, {p_part}. Role: {sig.role.value}."
    )


def validate_explanation(candidate: RootCauseCandidate) -> None:
    """Fail if explanation is empty or numbers disagree with struct fields."""
    if not candidate.explanation or not candidate.explanation.strip():
        raise ValueError(f"Empty explanation for {candidate.node_id}")

    for label, value in (("T", candidate.T), ("D", candidate.D), ("P", candidate.P)):
        pattern = rf"{label}=(\d+\.\d+)"
        m = re.search(pattern, candidate.explanation)
        if not m:
            raise ValueError(
                f"Explanation for {candidate.node_id} missing {label}=…"
            )
        explained = float(m.group(1))
        if abs(explained - round(value, 2)) > 1e-6 and abs(explained - value) > 0.015:
            raise ValueError(
                f"Explanation {label}={explained} != struct {value} "
                f"for {candidate.node_id}"
            )


def _node_dev_vector(anomaly: KernelAnomaly, node_id: str) -> dict[str, float]:
    """Pass through C2 metric ``dev_vector`` when present; else anomaly fallback."""
    metrics = anomaly.dev_vector.get(node_id) if anomaly.dev_vector else None
    if metrics:
        return dict(metrics)
    return {"anomaly": anomaly.anomaly_score.get(node_id, 0.0)}


def resolve_origins(candidates: list[RootCauseCandidate]) -> None:
    """Keep ORIGIN on the top-ranked candidate only; demote the rest in place.

    ORIGIN is a claim about the INCIDENT — "this is what caused it" — but
    ``derive_role`` decides it from one node's own signals, which cannot express
    a comparative claim. The two readings disagree wherever the influence graph
    has more than one source, and that is the common case: D is a ratio of
    outward to inward influence, so ANY zero-in-degree node scores D=1.0 by
    construction, clears the ORIGIN threshold, and is published as a cause. S5
    injects one fault and reported three origins that way — the real root plus a
    barely-deviating dependency and an unrelated side branch, both of which the
    ranking had already placed 2nd and 4th on combined T/D/P evidence.

    This runs after ranking (and after arbitration, which can reorder the top),
    so the label follows the evidence the score integrated rather than a
    threshold on one signal. Because ``score`` is invoked per cluster, "top" is
    per cluster: a genuine multi-fault incident keeps one origin per independent
    cluster rather than collapsing to a single global winner.

    Demoted nodes become AMPLIFIER when they still propagate degradation onward
    (P > 0 — some outgoing influence is both temporally consistent and
    degraded), else UNKNOWN. UNKNOWN is deliberate: a node that deviated, causes
    nothing downstream, and is not itself explained by anything upstream is
    genuinely uncharacterized, and saying so beats inventing a role for it.

    Does NOT touch VICTIM_OF_INFRA — that is arbitration's cross-layer verdict,
    which is a different axis from origin-vs-not.

    Callers MUST pass ``candidates`` already sorted by descending score.
    """
    for index, candidate in enumerate(candidates):
        if index == 0 or candidate.role != Role.ORIGIN:
            continue
        candidate.role = Role.AMPLIFIER if candidate.P > 0.0 else Role.UNKNOWN
        # Keep the templated explanation's role suffix in sync, as arbitration does.
        if ". Role: " in candidate.explanation:
            candidate.explanation = (
                candidate.explanation.rsplit(". Role: ", 1)[0]
                + f". Role: {candidate.role.value}."
            )
        candidate.flags["demoted_secondary_origin"] = True


def stamp_confidence(candidates: list[RootCauseCandidate]) -> None:
    """Margin confidence from spec §5, optionally isotonic-mapped from fit artifact.

    Top: ``S(top) * (S(top) - S(second)) / (S(top) + ε)``, exactly per spec.
    Others: the same local-margin formula, then clamped by a running minimum.

    When ``load_calibration_map()`` returns a non-degenerate mapping (≥10 points,
    non-constant), each raw value is passed through that map before the monotone
    clamp. Missing/unsafe artifacts leave raw confidence unchanged.

    The clamp enforces the invariant the raw formula violates: confidence must
    be non-increasing with rank. Without it a mid-list candidate sitting above
    a big gap outscores the top on confidence, and the last candidate — which
    compares against a 0.0 sentinel and so appears infinitely separated from
    nothing — keeps its full score. ``insert_report`` persists ``top.confidence``
    as the report's ``confidence_score``, so a non-monotone stamp makes the
    persisted number uninterpretable as a ranking confidence.

    Callers MUST pass ``candidates`` already sorted by descending score.
    """
    mapping = load_calibration_map()
    n = len(candidates)
    ceiling = float("inf")
    for i, c in enumerate(candidates):
        next_score = candidates[i + 1].score if i + 1 < n else 0.0
        margin = max(0.0, c.score - next_score)
        raw = c.score * margin / (c.score + EPS)
        conf = apply_calibration_map(raw, mapping) if mapping else raw
        c.confidence = min(conf, ceiling)
        ceiling = c.confidence


def build_candidates(
    signals: SignalResult,
    scores: dict[str, float],
    anomaly: KernelAnomaly,
    ig: InfluenceGraph,
    *,
    cluster_id: str | None = None,
) -> list[RootCauseCandidate]:
    candidates: list[RootCauseCandidate] = []
    for nid, sig in signals.by_node.items():
        role = sig.role if isinstance(sig.role, Role) else Role(sig.role)
        expl = render_explanation(nid, sig, anomaly, ig)
        node = ig.nodes.get(nid)
        kind = node.kind if node is not None else NodeKind.SERVICE
        cand = RootCauseCandidate(
            node_id=nid,
            node_kind=kind,
            score=scores.get(nid, 0.0),
            confidence=0.0,  # stamped after final ranking
            role=role,
            signals=SignalScores(T=sig.T, D=sig.D, P=sig.P),
            dev_vector=_node_dev_vector(anomaly, nid),
            explanation=expl,
            cluster_id=cluster_id,
            flags=dict(sig.flags),
        )
        validate_explanation(cand)
        candidates.append(cand)

    candidates.sort(key=lambda c: (-c.score, c.node_id))
    stamp_confidence(candidates)
    return candidates
