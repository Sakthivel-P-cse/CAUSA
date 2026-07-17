"""RootCauseCandidate emission + templated explanation sentences."""

from __future__ import annotations

import re

from contracts.types import AnomalyOutput, Role, RootCauseCandidate
from graphs.influence_graph import InfluenceGraph

from .signals import NodeSignals, SignalResult


def _fmt(x: float) -> str:
    return f"{x:.2f}"


def render_explanation(
    node_id: str,
    sig: NodeSignals,
    anomaly: AnomalyOutput,
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


def build_candidates(
    signals: SignalResult,
    scores: dict[str, float],
    anomaly: AnomalyOutput,
    ig: InfluenceGraph,
    *,
    cluster_id: str | None = None,
) -> list[RootCauseCandidate]:
    candidates: list[RootCauseCandidate] = []
    for nid, sig in signals.by_node.items():
        # Role may already be overridden by arbitration later.
        role = sig.role if isinstance(sig.role, Role) else Role(sig.role)
        expl = render_explanation(nid, sig, anomaly, ig)
        cand = RootCauseCandidate(
            node_id=nid,
            score=scores.get(nid, 0.0),
            T=sig.T,
            D=sig.D,
            P=sig.P,
            role=role,
            dev_vector={
                "anomaly": anomaly.anomaly_score.get(nid, 0.0),
                "T": sig.T,
                "D": sig.D,
                "P": sig.P,
                "first_deviation": anomaly.first_deviation_time.get(nid)  # type: ignore[dict-item]
                if anomaly.first_deviation_time.get(nid) is not None
                else -1.0,
            },
            explanation=expl,
            cluster_id=cluster_id,
            flags=dict(sig.flags),
        )
        validate_explanation(cand)
        candidates.append(cand)

    candidates.sort(key=lambda c: (-c.score, c.node_id))
    return candidates
