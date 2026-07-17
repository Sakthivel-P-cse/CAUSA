"""T / D / P signal computation on the Influence Graph."""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts.types import AnomalyOutput, Role
from graphs.influence_graph import InfluenceGraph

EPS = 1e-9
ALPHA = 0.5
H_DEFAULT = 60.0  # seconds


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class NodeSignals:
    node_id: str
    T: float
    D: float
    P: float
    role: Role
    flags: dict[str, bool] = field(default_factory=dict)
    # Diagnostics used by explanations / P.
    out_influence: float = 0.0
    in_influence: float = 0.0
    p_consistent: int = 0
    p_total: int = 0
    betweenness: float = 0.0


@dataclass
class SignalResult:
    by_node: dict[str, NodeSignals]
    t0: float
    horizon: float


def _max_subgraph_delta(anomaly: AnomalyOutput, node_ids: set[str]) -> float:
    times = [
        t
        for nid, t in anomaly.first_deviation_time.items()
        if nid in node_ids and t is not None
    ]
    if not times:
        return 0.0
    return max(times) - min(times)


def compute_T(
    node_id: str,
    anomaly: AnomalyOutput,
    horizon: float,
) -> tuple[float, dict[str, bool]]:
    """Temporal primacy — time-delta, not rank.

    No-deviation / slow-burn → T=0.5 flagged neutral.
    """
    flags: dict[str, bool] = {}
    fdt = anomaly.first_deviation_time.get(node_id)
    if fdt is None:
        flags["slow_burn_T"] = True
        return 0.5, flags

    t0 = anomaly.t0
    T = clamp01(1.0 - (fdt - t0) / (horizon + EPS))
    return T, flags


def _influence_weight(anomaly: AnomalyOutput, node_id: str) -> float:
    return max(0.0, anomaly.anomaly_score.get(node_id, 0.0))


def compute_D(
    node_id: str,
    ig: InfluenceGraph,
    anomaly: AnomalyOutput,
    alpha: float = ALPHA,
) -> tuple[float, float, float]:
    """Cascade direction on IG.

    D = (α·a + Out) / (α·a + Out + In + ε)
    """
    a = anomaly.anomaly_score.get(node_id, 0.0)
    out_inf = sum(
        _influence_weight(anomaly, e.dst) for e in ig.out_edges(node_id)
    )
    # Also count structural out-degree lightly so a pure origin with low
    # downstream anomaly still gets OutInfluence > 0.
    out_inf += 0.1 * ig.out_degree(node_id)

    in_inf = sum(
        _influence_weight(anomaly, e.src) for e in ig.in_edges(node_id)
    )
    in_inf += 0.1 * ig.in_degree(node_id)

    D = (alpha * a + out_inf) / (alpha * a + out_inf + in_inf + EPS)
    return clamp01(D), out_inf, in_inf


def compute_P(
    node_id: str,
    ig: InfluenceGraph,
    anomaly: AnomalyOutput,
) -> tuple[float, int, int, dict[str, bool]]:
    """Propagation consistency on IG.

    Fraction of outgoing influence edges N→M where N deviated before M AND
    the edge metric degraded, anomaly-weighted.
    Zero out-degree → P=0, flagged.
    """
    flags: dict[str, bool] = {}
    out = ig.out_edges(node_id)
    if not out:
        flags["zero_outdegree_P"] = True
        return 0.0, 0, 0, flags

    fdt_n = anomaly.first_deviation_time.get(node_id)
    weighted_ok = 0.0
    weighted_total = 0.0
    consistent = 0
    total = 0

    for e in out:
        w = max(anomaly.anomaly_score.get(e.dst, 0.0), 0.05)
        weighted_total += w
        total += 1

        fdt_m = anomaly.first_deviation_time.get(e.dst)
        # If N has no deviation time, it cannot satisfy "before"; edge fails
        # temporal check (slow-burn origins still rank via T/D).
        before = (
            fdt_n is not None
            and fdt_m is not None
            and fdt_n <= fdt_m
        )
        degraded = e.degraded
        if before and degraded:
            weighted_ok += w
            consistent += 1
        elif before and not degraded:
            # Temporal order ok but metric not marked degraded — partial credit.
            weighted_ok += 0.5 * w
            consistent += 1  # count as order-consistent for explanation nums

    P = weighted_ok / (weighted_total + EPS) if weighted_total > 0 else 0.0
    return clamp01(P), consistent, total, flags


def _approx_betweenness(ig: InfluenceGraph) -> dict[str, float]:
    """Cheap undirected degree-centrality proxy for role derivation.

    Full pairwise shortest-path betweenness is unnecessary for role labels;
    high (in+out) degree stands in for "high betweenness + incoming".
    """
    if not ig.nodes:
        return {}
    raw = {
        nid: float(ig.in_degree(nid) + ig.out_degree(nid))
        for nid in ig.nodes
    }
    mx = max(raw.values()) or 1.0
    return {nid: v / mx for nid, v in raw.items()}


def derive_role(
    D: float,
    anomaly_score: float,
    betweenness: float,
    in_influence: float,
) -> Role:
    """origin: high D + high anomaly; amplifier: mid D + high betweenness/in; else victim."""
    if D >= 0.55 and anomaly_score >= 0.5:
        return Role.ORIGIN
    if 0.35 <= D < 0.55 and (betweenness >= 0.5 or in_influence >= 0.5):
        return Role.AMPLIFIER
    if D < 0.45:
        return Role.VICTIM
    # Mid/high D but modest anomaly — still lean origin if outward-heavy.
    if D >= 0.55:
        return Role.ORIGIN
    return Role.UNKNOWN


def compute_signals(
    ig: InfluenceGraph,
    anomaly: AnomalyOutput,
    *,
    alpha: float = ALPHA,
    H: float = H_DEFAULT,
) -> SignalResult:
    node_ids = ig.node_ids()
    max_delta = _max_subgraph_delta(anomaly, node_ids)
    horizon = min(max_delta, H) if max_delta > 0 else H
    # Avoid degenerate horizon of 0.
    if horizon <= 0:
        horizon = H

    betweenness = _approx_betweenness(ig)
    by_node: dict[str, NodeSignals] = {}

    for nid in node_ids:
        T, t_flags = compute_T(nid, anomaly, horizon)
        D, out_inf, in_inf = compute_D(nid, ig, anomaly, alpha=alpha)
        P, p_ok, p_tot, p_flags = compute_P(nid, ig, anomaly)
        a = anomaly.anomaly_score.get(nid, 0.0)
        role = derive_role(D, a, betweenness.get(nid, 0.0), in_inf)
        flags = {**t_flags, **p_flags}
        by_node[nid] = NodeSignals(
            node_id=nid,
            T=T,
            D=D,
            P=P,
            role=role,
            flags=flags,
            out_influence=out_inf,
            in_influence=in_inf,
            p_consistent=p_ok,
            p_total=p_tot,
            betweenness=betweenness.get(nid, 0.0),
        )

    return SignalResult(by_node=by_node, t0=anomaly.t0, horizon=horizon)
