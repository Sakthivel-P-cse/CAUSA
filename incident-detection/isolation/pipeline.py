"""Multi-fault isolation on IG ∩ incident subgraph.

Stages:
  1. Connected components — cheap first cut
  2. Gap repair — merge false splits via quiet-relay paths (θ_low + monotonic time)
  3. Origin clustering — split false merges (temporal sources + Δ + reachability)
  4. Independent scoring per cluster

Abstain with "MULTI-INCIDENT — ambiguous" when faults are causally adjacent.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from contracts import RankedResult
from graphs.incident_subgraph import THETA_LOW, IncidentSubgraph
from graphs.influence_graph import InfluenceGraph, build_influence_graph
from rca.types import KernelAnomaly
from scoring.scorer import score

DEFAULT_DELTA = 5.0  # coupling window seconds


@dataclass
class IsolationResult:
    clusters: list[set[str]]
    ranked_by_cluster: dict[str, RankedResult] = field(default_factory=dict)
    abstain_reason: str | None = None

    @property
    def cluster_count(self) -> int:
        return len(self.clusters)


def _undirected_components(ig: InfluenceGraph) -> list[set[str]]:
    seen: set[str] = set()
    comps: list[set[str]] = []
    adj: dict[str, set[str]] = {nid: set() for nid in ig.nodes}
    for e in ig.edges:
        adj[e.src].add(e.dst)
        adj[e.dst].add(e.src)

    for start in ig.nodes:
        if start in seen:
            continue
        stack = [start]
        comp: set[str] = set()
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            comp.add(n)
            stack.extend(adj[n] - seen)
        comps.append(comp)
    return comps


def _path_exists_quiet_relay(
    ig: InfluenceGraph,
    anomaly: KernelAnomaly,
    a: str,
    b: str,
    theta_low: float,
) -> bool:
    """True if a path of quiet-relay nodes connects a and b with monotonic time."""
    # BFS on undirected view; nodes on path (excluding endpoints) must be
    # ≤ theta_low anomaly (quiet), and first_deviation times along the path must
    # be non-decreasing.
    #
    # The two constraints need different handling, which the previous global
    # `visited` set got wrong. Quietness is a NODE property, so filtering on it
    # during expansion is sound. Monotonic time is a PATH property — marking a
    # node visited on enqueue meant only the first path reaching it was ever
    # examined, so a path failing the time check aborted the search rather than
    # letting an alternative route through. Gap repair then silently
    # under-merged clusters that a valid quiet relay did connect.
    #
    # State is therefore (node, last_seen_deviation_time), pruned by dominance:
    # reaching a node with a SMALLER carried time is strictly more permissive
    # for every continuation, so only the minimum per node is worth expanding.
    # That keeps the search bounded without discarding viable paths.
    adj: dict[str, set[str]] = {nid: set() for nid in ig.nodes}
    for e in ig.edges:
        adj.setdefault(e.src, set()).add(e.dst)
        adj.setdefault(e.dst, set()).add(e.src)

    from collections import deque

    neg_inf, inf = float("-inf"), float("inf")
    start = anomaly.first_deviation_time.get(a)
    start_t = float(start) if start is not None else neg_inf

    q: deque[tuple[str, float]] = deque([(a, start_t)])
    best: dict[str, float] = {a: start_t}
    while q:
        node, last_t = q.popleft()
        for nbr in adj.get(node, ()):
            t = anomaly.first_deviation_time.get(nbr)
            if t is not None and float(t) < last_t:
                continue  # would break non-decreasing deviation order
            # A node with no FirstDeviationTime doesn't constrain the order.
            new_last = float(t) if t is not None else last_t
            if nbr == b:
                return True  # endpoints are exempt from the quiet requirement
            if anomaly.anomaly_score.get(nbr, 0.0) > theta_low:
                continue  # intermediates must be quiet relays
            if best.get(nbr, inf) <= new_last:
                continue  # already reached under a looser time constraint
            best[nbr] = new_last
            q.append((nbr, new_last))
    return False


def _gap_repair(
    components: list[set[str]],
    ig: InfluenceGraph,
    anomaly: KernelAnomaly,
    theta_low: float,
) -> list[set[str]]:
    """Merge components connected by quiet-relay paths."""
    comps = [set(c) for c in components]
    merged = True
    while merged:
        merged = False
        for i in range(len(comps)):
            for j in range(i + 1, len(comps)):
                # Try endpoints: high-anomaly nodes in each component.
                seeds_i = sorted(
                    comps[i],
                    key=lambda n: anomaly.anomaly_score.get(n, 0.0),
                    reverse=True,
                )[:3]
                seeds_j = sorted(
                    comps[j],
                    key=lambda n: anomaly.anomaly_score.get(n, 0.0),
                    reverse=True,
                )[:3]
                link = False
                for a in seeds_i:
                    for b in seeds_j:
                        if _path_exists_quiet_relay(ig, anomaly, a, b, theta_low):
                            link = True
                            break
                    if link:
                        break
                if link:
                    comps[i] = comps[i] | comps[j]
                    del comps[j]
                    merged = True
                    break
            if merged:
                break
    return comps


def _temporal_origins(
    node_ids: set[str],
    anomaly: KernelAnomaly,
    ig: InfluenceGraph,
    *,
    min_anomaly: float = 0.5,
    strong_anomaly: float = 0.7,
) -> list[str]:
    """Local temporal sources on IG for origin-clustering.

    A node is a local source iff it has a FirstDeviationTime, anomaly >=
    min_anomaly, and no IG-predecessor in the component deviant earlier.
    For clustering we further keep only *strong* origins (anomaly >=
    strong_anomaly) or the unique earliest local source — this prevents
    mid-cascade side branches (e.g. payment in S0) from splitting a
    single fault.
    """
    local: list[tuple[str, float]] = []
    for nid in sorted(node_ids):
        fdt = anomaly.first_deviation_time.get(nid)
        if fdt is None:
            continue
        if anomaly.anomaly_score.get(nid, 0.0) < min_anomaly:
            continue
        earlier_pred = False
        for p in ig.predecessors(nid):
            if p not in node_ids:
                continue
            pt = anomaly.first_deviation_time.get(p)
            if pt is None:
                continue
            if pt <= fdt and anomaly.anomaly_score.get(p, 0.0) >= min_anomaly:
                earlier_pred = True
                break
        if not earlier_pred:
            local.append((nid, fdt))
    if not local:
        return []
    earliest = min(t for _, t in local)
    origins: list[str] = []
    for nid, t in local:
        a = anomaly.anomaly_score.get(nid, 0.0)
        if a >= strong_anomaly or abs(t - earliest) <= 1e-9:
            origins.append(nid)
    return origins


def _reachable(ig: InfluenceGraph, start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(ig.successors(n))
    return seen


def _origin_clustering(
    component: set[str],
    ig: InfluenceGraph,
    anomaly: KernelAnomaly,
    delta: float,
) -> list[set[str]] | str:
    """Split a component into origin clusters, or return abstain reason."""
    origins = _temporal_origins(component, anomaly, ig)
    if len(origins) <= 1:
        return [set(component)]

    # Pairwise: if two origins are within Δ and each can reach the other
    # (or share reachable set heavily), they are causally adjacent → abstain.
    for i, o1 in enumerate(origins):
        t1 = anomaly.first_deviation_time.get(o1)
        if t1 is None:
            continue
        r1 = _reachable(ig, o1) & component
        for o2 in origins[i + 1 :]:
            t2 = anomaly.first_deviation_time.get(o2)
            if t2 is None:
                continue
            if abs(t1 - t2) <= delta:
                r2 = _reachable(ig, o2) & component
                shared = (r1 & r2) - {o1, o2}
                # Shared victim or mutual reachability => causally adjacent.
                if o1 in r2 or o2 in r1 or shared:
                    return "MULTI-INCIDENT — ambiguous"

    # Otherwise partition by nearest temporal origin via IG reachability.
    clusters: dict[str, set[str]] = {o: {o} for o in origins}
    assigned = set(origins)
    for nid in component:
        if nid in assigned:
            continue
        # Assign to origin that can reach nid, preferring earliest.
        candidates = []
        for o in origins:
            if nid in _reachable(ig, o):
                t = anomaly.first_deviation_time.get(o, float("inf"))
                candidates.append((t if t is not None else float("inf"), o))
        if candidates:
            candidates.sort()
            clusters[candidates[0][1]].add(nid)
            assigned.add(nid)
        else:
            # Unreachable from any origin — attach to earliest origin.
            clusters[origins[0]].add(nid)
            assigned.add(nid)

    return list(clusters.values())


def _restrict_subgraph(
    subgraph: IncidentSubgraph,
    node_ids: set[str],
) -> IncidentSubgraph:
    nodes = {nid: n for nid, n in subgraph.nodes.items() if nid in node_ids}
    edges = [
        e for e in subgraph.edges if e.src in node_ids and e.dst in node_ids
    ]
    return IncidentSubgraph(
        nodes=nodes,
        edges=edges,
        anomaly=subgraph.anomaly,
        theta_fire=subgraph.theta_fire,
        theta_low=subgraph.theta_low,
        # Carry the direct-evidence basis through, or each cluster silently
        # renormalizes time over its full membership including expanded
        # neighbours (see IncidentSubgraph.seed_ids).
        seed_ids=subgraph.seed_ids & node_ids,
    )


def _shared_horizon(
    subgraph: IncidentSubgraph,
    anomaly: KernelAnomaly,
    clusters: list[set[str]],
) -> float | None:
    """One timescale for every cluster: the largest within-cluster spread.

    Measured per cluster and maximized, NOT taken across all clusters at once —
    the gap between two independent incidents is not part of either one's
    propagation timescale, and folding it in would flatten T everywhere.
    Returns None when no cluster has two timed nodes, leaving the default.
    """
    spreads: list[float] = []
    for cluster in clusters:
        basis = (subgraph.seed_ids & cluster) or cluster
        times = [
            t
            for nid, t in anomaly.first_deviation_time.items()
            if nid in basis and t is not None
        ]
        if len(times) >= 2:
            spreads.append(max(times) - min(times))
    if not spreads:
        return None
    widest = max(spreads)
    return widest if widest > 0 else None


def isolate(
    subgraph: IncidentSubgraph,
    *,
    ig: InfluenceGraph | None = None,
    anomaly: KernelAnomaly | None = None,
    delta: float = DEFAULT_DELTA,
    theta_low: float = THETA_LOW,
    weights: dict[str, float] | None = None,
    enabled_signals: Iterable[str] | None = None,
    alerted_service: str | None = None,
) -> IsolationResult:
    """`weights` / `enabled_signals` pass straight through to `score`, so an
    ablation or sweep can run the real production path rather than bypassing
    isolation and measuring something the engine never executes."""
    anomaly = anomaly or subgraph.anomaly
    if anomaly is None:
        raise ValueError("anomaly required")

    ig = ig or build_influence_graph(subgraph)

    # Stage 1
    comps = _undirected_components(ig)

    # Stage 2
    comps = _gap_repair(comps, ig, anomaly, theta_low)

    # Stage 3
    final_clusters: list[set[str]] = []
    for comp in comps:
        result = _origin_clustering(comp, ig, anomaly, delta)
        if isinstance(result, str):
            return IsolationResult(clusters=[], abstain_reason=result)
        final_clusters.extend(result)

    # Stage 4 — independent scoring per cluster, on ONE shared timescale.
    #
    # Each cluster keeps its own t0 (its own incident start), but the horizon is
    # computed once over every direct-evidence node and passed to all of them.
    # Scoring each cluster in isolation derived the horizon from that cluster's
    # own spread, so a 3s cluster and a 50s cluster produced T values on
    # different normalizations — and the orchestrator then merges every
    # cluster's candidates and sorts by raw score, comparing them as if
    # commensurable. A shared horizon makes that merge meaningful.
    shared_horizon = _shared_horizon(subgraph, anomaly, final_clusters)

    ranked_by_cluster: dict[str, RankedResult] = {}
    for i, cluster in enumerate(final_clusters):
        cid = f"cluster-{i}"
        sub = _restrict_subgraph(subgraph, cluster)
        # Build IG scoped to cluster nodes.
        cluster_ig = build_influence_graph(sub)
        result = score(
            sub,
            weights,
            enabled_signals,
            ig=cluster_ig,
            anomaly=anomaly,
            horizon=shared_horizon,
            alerted_service=alerted_service,
        )
        # Stamp cluster_id
        for c in result.candidates:
            c.cluster_id = cid
        ranked_by_cluster[cid] = result

    return IsolationResult(
        clusters=final_clusters,
        ranked_by_cluster=ranked_by_cluster,
    )
