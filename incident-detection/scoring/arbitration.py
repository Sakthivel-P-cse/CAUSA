"""Layer arbitration — post-scoring re-rank of infra vs hosted service."""

from __future__ import annotations

from contracts import CallType, Role, RootCauseCandidate
from graphs.influence_graph import InfluenceGraph
from ingest.service_scope import is_system_infra_host
from rca.types import KernelAnomaly


def arbitrate(
    candidates: list[RootCauseCandidate],
    ig: InfluenceGraph,
    anomaly: KernelAnomaly,
    *,
    top_c: int = 5,
    alerted_service: str | None = None,
) -> list[RootCauseCandidate]:
    """Promote infra I above hosted service N when both are top-C and I leads.

    Conditions (all required):
      - I → N is a runs-on influence edge in IG
      - both appear in the top-C scored candidates
      - FirstDeviationTime(I) < FirstDeviationTime(N)
      - anomaly_score(I) ≥ anomaly_score(N)
      - I is not a Kind/k8s control-plane style system host
      - if alerted_service is set, I must host that service (runs-on affinity)

    Then promote I above N and set N.role = victim-of-infra.
    """
    if not candidates:
        return candidates

    ranked = sorted(candidates, key=lambda c: (-c.score, c.node_id))
    top_ids = {c.node_id for c in ranked[:top_c]}

    promotions: list[tuple[str, str]] = []
    for e in ig.edges:
        if e.call_type != CallType.RUNS_ON:
            continue
        infra_id, service_id = e.src, e.dst
        if is_system_infra_host(infra_id):
            continue
        if infra_id not in top_ids or service_id not in top_ids:
            continue
        if alerted_service and not any(
            edge.call_type == CallType.RUNS_ON
            and edge.src == infra_id
            and edge.dst == alerted_service
            for edge in ig.edges
        ):
            continue

        t_i = anomaly.first_deviation_time.get(infra_id)
        t_n = anomaly.first_deviation_time.get(service_id)
        if t_i is None or t_n is None or not (t_i < t_n):
            continue

        a_i = anomaly.anomaly_score.get(infra_id, 0.0)
        a_n = anomaly.anomaly_score.get(service_id, 0.0)
        if a_i < a_n:
            continue

        promotions.append((infra_id, service_id))

    if not promotions:
        return ranked

    by_id = {c.node_id: c for c in ranked}

    for infra_id, service_id in promotions:
        svc = by_id.get(service_id)
        infra = by_id.get(infra_id)
        if svc is None or infra is None:
            continue
        svc.role = Role.VICTIM_OF_INFRA
        if ". Role: " in svc.explanation:
            svc.explanation = (
                svc.explanation.rsplit(". Role: ", 1)[0]
                + f". Role: {svc.role.value}."
            )
        if infra.score < svc.score:
            infra.score, svc.score = svc.score, infra.score

    ranked = sorted(ranked, key=lambda c: (-c.score, c.node_id))

    for infra_id, service_id in promotions:
        idx = {c.node_id: i for i, c in enumerate(ranked)}
        if infra_id not in idx or service_id not in idx:
            continue
        if idx[infra_id] > idx[service_id]:
            ranked[idx[infra_id]].score = ranked[idx[service_id]].score + 1e-3
            ranked = sorted(ranked, key=lambda c: (-c.score, c.node_id))

    return ranked
