"""Isolation view — multi-fault eval seam without coupling to Dev B isolation/.

Dev B's IsolationResult maps here via from_isolation_result. Eval metrics depend
only on this view + DualInjectionGroundTruth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contracts import ABSTENTION_FLAG, RankedCluster, RankedResult, RootCauseCandidate


@dataclass
class IsolationView:
    """Predicted multi-fault partition + per-cluster rankings."""

    incident_id: str
    clusters: list[set[str]] = field(default_factory=list)
    ranked_by_cluster: dict[str, RankedResult] = field(default_factory=dict)
    abstain_reason: str | None = None

    @property
    def abstained(self) -> bool:
        return self.abstain_reason is not None

    @property
    def cluster_count(self) -> int:
        return 0 if self.abstained else len(self.clusters)

    def predicted_partition(self) -> dict[str, int]:
        mapping: dict[str, int] = {}
        for i, members in enumerate(self.clusters):
            for nid in members:
                mapping[nid] = i
        return mapping

    def ranked_lists(self) -> list[list[str]]:
        lists: list[list[str]] = []
        for i, members in enumerate(self.clusters):
            key = str(i)
            if key in self.ranked_by_cluster:
                lists.append(self.ranked_by_cluster[key].ranked_node_ids())
            else:
                for k, rr in self.ranked_by_cluster.items():
                    if set(rr.ranked_node_ids()) & members:
                        lists.append(rr.ranked_node_ids())
                        break
                else:
                    lists.append(sorted(members))
        return lists

    @classmethod
    def abstain(cls, incident_id: str, reason: str = ABSTENTION_FLAG) -> IsolationView:
        return cls(incident_id=incident_id, abstain_reason=reason)

    @classmethod
    def from_partitions(
        cls,
        incident_id: str,
        partitions: list[set[str]],
        ranked_results: list[RankedResult] | None = None,
        abstain_reason: str | None = None,
    ) -> IsolationView:
        ranked: dict[str, RankedResult] = {}
        if ranked_results:
            for i, rr in enumerate(ranked_results):
                ranked[str(i)] = rr
        return cls(
            incident_id=incident_id,
            clusters=[set(p) for p in partitions],
            ranked_by_cluster=ranked,
            abstain_reason=abstain_reason,
        )

    @classmethod
    def from_ranked_clusters(
        cls,
        result: RankedResult,
        clusters: list[RankedCluster],
    ) -> IsolationView:
        """Build a view from explicit RankedCluster helpers (tests / fixtures)."""
        if result.abstained:
            return cls.abstain(result.incident_id, result.abstain_reason or ABSTENTION_FLAG)
        partitions: list[set[str]] = []
        ranked: dict[str, RankedResult] = {}
        for i, cl in enumerate(clusters):
            members = set(cl.node_ids or [c.node_id for c in cl.candidates])
            partitions.append(members)
            ranked[str(i)] = RankedResult(
                incident_id=result.incident_id,
                candidates=list(cl.candidates),
                fault_class=result.fault_class,
            )
        return cls(
            incident_id=result.incident_id,
            clusters=partitions,
            ranked_by_cluster=ranked,
            abstain_reason=result.abstain_reason,
        )

    @classmethod
    def from_candidates_with_cluster_ids(cls, result: RankedResult) -> IsolationView:
        """Group flat candidates by cluster_id when present."""
        if result.abstained:
            return cls.abstain(result.incident_id, result.abstain_reason or ABSTENTION_FLAG)
        by_id: dict[str, list[RootCauseCandidate]] = {}
        for c in result.candidates:
            key = c.cluster_id or "0"
            by_id.setdefault(key, []).append(c)
        partitions: list[set[str]] = []
        ranked: dict[str, RankedResult] = {}
        for i, (key, cands) in enumerate(by_id.items()):
            partitions.append({c.node_id for c in cands})
            ranked[str(i)] = RankedResult(
                incident_id=result.incident_id,
                candidates=list(cands),
                fault_class=result.fault_class,
            )
        return cls(
            incident_id=result.incident_id,
            clusters=partitions,
            ranked_by_cluster=ranked,
            abstain_reason=None,
        )

    @classmethod
    def from_isolation_result(
        cls,
        result: Any,
        incident_id: str = "",
    ) -> IsolationView:
        """Map Dev B isolation.IsolationResult → IsolationView."""
        reason = getattr(result, "abstain_reason", None)
        iid = incident_id or ""
        ranked_raw = getattr(result, "ranked_by_cluster", {}) or {}
        if not iid:
            for rr in ranked_raw.values():
                if getattr(rr, "incident_id", ""):
                    iid = rr.incident_id
                    break
        if reason:
            return cls.abstain(iid or "unknown", reason)

        clusters = [set(c) for c in (getattr(result, "clusters", None) or [])]
        ranked: dict[str, RankedResult] = {}
        for key, rr in ranked_raw.items():
            if key.startswith("cluster-"):
                idx = key.split("-", 1)[1]
            else:
                idx = str(key)
            ranked[idx] = rr
        if not ranked and clusters:
            for i, _ in enumerate(clusters):
                alt = ranked_raw.get(f"cluster-{i}") or ranked_raw.get(str(i))
                if alt is not None:
                    ranked[str(i)] = alt
        return cls(
            incident_id=iid or "unknown",
            clusters=clusters,
            ranked_by_cluster=ranked,
            abstain_reason=None,
        )
