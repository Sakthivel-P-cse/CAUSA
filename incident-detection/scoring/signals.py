"""T / D / P signal computation on the Influence Graph.

Math matches ``RCA_Engine_Spec.md`` §2:
  - T: ``t0 = min FirstDeviationTime`` over subgraph nodes that have one
  - D: pure Out/InInfluence = Σ anomaly_score of successors/predecessors
  - P: consistent only when temporal order AND edge.degraded
  - betweenness: undirected shortest-path (Brandes), for amplifier role
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from contracts import Role
from graphs.influence_graph import InfluenceGraph
from rca.types import KernelAnomaly

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


def _subgraph_t0(anomaly: KernelAnomaly, node_ids: set[str]) -> float:
    """Earliest FirstDeviationTime among subgraph nodes that have one."""
    times = [
        t
        for nid, t in anomaly.first_deviation_time.items()
        if nid in node_ids and t is not None
    ]
    if not times:
        return anomaly.t0
    return min(times)


def _max_subgraph_delta(anomaly: KernelAnomaly, node_ids: set[str]) -> float:
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
    anomaly: KernelAnomaly,
    horizon: float,
    t0: float,
) -> tuple[float, dict[str, bool]]:
    """Temporal primacy — time-delta, not rank.

    No-deviation / slow-burn → T=0.5 flagged neutral.
    ``t0`` is the subgraph-wide min FirstDeviationTime (spec §2.1).
    """
    flags: dict[str, bool] = {}
    fdt = anomaly.first_deviation_time.get(node_id)
    if fdt is None:
        flags["slow_burn_T"] = True
        return 0.5, flags

    T = clamp01(1.0 - (fdt - t0) / (horizon + EPS))
    return T, flags


def _influence_weight(anomaly: KernelAnomaly, node_id: str) -> float:
    return max(0.0, anomaly.anomaly_score.get(node_id, 0.0))


def compute_D(
    node_id: str,
    ig: InfluenceGraph,
    anomaly: KernelAnomaly,
    alpha: float = ALPHA,
) -> tuple[float, float, float]:
    """Cascade direction on IG (spec §2.2).

    OutInfluence / InInfluence = pure Σ anomaly_score of successors /
    predecessors — no degree fudge term.
    """
    a = anomaly.anomaly_score.get(node_id, 0.0)
    out_inf = sum(
        _influence_weight(anomaly, e.dst) for e in ig.out_edges(node_id)
    )
    in_inf = sum(
        _influence_weight(anomaly, e.src) for e in ig.in_edges(node_id)
    )

    D = (alpha * a + out_inf) / (alpha * a + out_inf + in_inf + EPS)
    return clamp01(D), out_inf, in_inf


def compute_P(
    node_id: str,
    ig: InfluenceGraph,
    anomaly: KernelAnomaly,
) -> tuple[float, int, int, dict[str, bool]]:
    """Propagation consistency on IG (spec §2.3).

    consistent(N→M) only when FirstDeviationTime(N) ≤ FirstDeviationTime(M)
    AND edge.degraded. No partial credit.
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
        w = max(anomaly.anomaly_score.get(e.dst, 0.0), EPS)
        weighted_total += w
        total += 1

        fdt_m = anomaly.first_deviation_time.get(e.dst)
        before = (
            fdt_n is not None
            and fdt_m is not None
            and fdt_n <= fdt_m
        )
        if before and e.degraded:
            weighted_ok += w
            consistent += 1

    P = weighted_ok / (weighted_total + EPS) if weighted_total > 0 else 0.0
    return clamp01(P), consistent, total, flags


def _betweenness_centrality(ig: InfluenceGraph) -> dict[str, float]:
    """Undirected shortest-path betweenness (Brandes), normalized to [0, 1]."""
    nodes = list(ig.nodes.keys())
    if len(nodes) <= 2:
        return {nid: 0.0 for nid in nodes}

    adj: dict[str, set[str]] = {nid: set() for nid in nodes}
    for e in ig.edges:
        adj[e.src].add(e.dst)
        adj[e.dst].add(e.src)

    betweenness = {nid: 0.0 for nid in nodes}

    for s in nodes:
        stack: list[str] = []
        pred: dict[str, list[str]] = {w: [] for w in nodes}
        sigma = {w: 0.0 for w in nodes}
        sigma[s] = 1.0
        dist = {w: -1 for w in nodes}
        dist[s] = 0
        q: deque[str] = deque([s])
        while q:
            v = q.popleft()
            stack.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    q.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = {w: 0.0 for w in nodes}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                betweenness[w] += delta[w]

    # Undirected: each pair counted twice.
    for nid in nodes:
        betweenness[nid] /= 2.0

    mx = max(betweenness.values()) or 1.0
    return {nid: v / mx for nid, v in betweenness.items()}


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
    anomaly: KernelAnomaly,
    *,
    alpha: float = ALPHA,
    H: float = H_DEFAULT,
) -> SignalResult:
    node_ids = ig.node_ids()
    t0 = _subgraph_t0(anomaly, node_ids)
    max_delta = _max_subgraph_delta(anomaly, node_ids)
    horizon = min(max_delta, H) if max_delta > 0 else H
    # Avoid degenerate horizon of 0.
    if horizon <= 0:
        horizon = H

    betweenness = _betweenness_centrality(ig)
    by_node: dict[str, NodeSignals] = {}

    for nid in node_ids:
        T, t_flags = compute_T(nid, anomaly, horizon, t0)
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

    return SignalResult(by_node=by_node, t0=t0, horizon=horizon)
