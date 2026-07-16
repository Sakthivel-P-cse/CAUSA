"""C3 — RootCauseCandidate / RankedResult (B → C).

Supports multi-fault isolation: k ranked lists + abstention flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeKind(str, Enum):
    SERVICE = "service"
    INFRA = "infra"
    DEPENDENCY = "dependency"


class NodeRole(str, Enum):
    ORIGIN = "origin"
    AMPLIFIER = "amplifier"
    VICTIM = "victim"
    VICTIM_OF_INFRA = "victim-of-infra"


class FaultClass(str, Enum):
    APP_LOGIC = "AppLogic"
    CONFIG = "Config"
    INFRA = "Infra"
    DEPENDENCY = "Dependency"
    RESOURCE = "Resource"
    DATA = "Data"


@dataclass
class SignalScores:
    T: float = 0.0
    D: float = 0.0
    P: float = 0.0


@dataclass
class RootCauseCandidate:
    node_id: str
    node_kind: NodeKind
    score: float
    confidence: float
    role: NodeRole
    signals: SignalScores
    dev_vector: dict[str, float] = field(default_factory=dict)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_kind": self.node_kind.value,
            "score": self.score,
            "confidence": self.confidence,
            "role": self.role.value,
            "signals": {"T": self.signals.T, "D": self.signals.D, "P": self.signals.P},
            "dev_vector": self.dev_vector,
            "explanation": self.explanation,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RootCauseCandidate:
        sig = d.get("signals") or {}
        return cls(
            node_id=d["node_id"],
            node_kind=NodeKind(d["node_kind"]),
            score=float(d["score"]),
            confidence=float(d.get("confidence", 0.0)),
            role=NodeRole(d["role"]),
            signals=SignalScores(
                T=float(sig.get("T", 0.0)),
                D=float(sig.get("D", 0.0)),
                P=float(sig.get("P", 0.0)),
            ),
            dev_vector=dict(d.get("dev_vector") or {}),
            explanation=str(d.get("explanation") or ""),
        )


@dataclass
class RankedCluster:
    """One ranked list for one isolated fault (spec §7 Stage 4)."""

    fault_id: str | None
    candidates: list[RootCauseCandidate] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)  # partition membership


ABSTENTION_FLAG = "MULTI-INCIDENT — ambiguous"


@dataclass
class RankedResult:
    """Scorer / isolation output.

    Single-fault: one cluster (or flat candidates via ``primary_candidates``).
    Multi-fault: ``clusters`` with k lists. Abstention sets ``abstained=True``.
    """

    incident_id: str
    clusters: list[RankedCluster] = field(default_factory=list)
    fault_class: FaultClass | None = None
    abstained: bool = False
    abstention_reason: str | None = None

    @property
    def primary_candidates(self) -> list[RootCauseCandidate]:
        if self.clusters:
            return self.clusters[0].candidates
        return []

    def ranked_node_ids(self, cluster_idx: int = 0) -> list[str]:
        if cluster_idx >= len(self.clusters):
            return []
        return [c.node_id for c in self.clusters[cluster_idx].candidates]

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "fault_class": self.fault_class.value if self.fault_class else None,
            "abstained": self.abstained,
            "abstention_reason": self.abstention_reason,
            "clusters": [
                {
                    "fault_id": cl.fault_id,
                    "node_ids": list(cl.node_ids),
                    "candidates": [c.to_dict() for c in cl.candidates],
                }
                for cl in self.clusters
            ],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RankedResult:
        clusters: list[RankedCluster] = []
        raw_clusters = d.get("clusters")
        if raw_clusters:
            for cl in raw_clusters:
                clusters.append(
                    RankedCluster(
                        fault_id=cl.get("fault_id"),
                        node_ids=list(cl.get("node_ids") or []),
                        candidates=[
                            RootCauseCandidate.from_dict(c) for c in (cl.get("candidates") or [])
                        ],
                    )
                )
        elif d.get("candidates"):
            # Backward-compatible flat list
            cands = [RootCauseCandidate.from_dict(c) for c in d["candidates"]]
            clusters = [
                RankedCluster(
                    fault_id=None,
                    candidates=cands,
                    node_ids=[c.node_id for c in cands],
                )
            ]
        fc = d.get("fault_class")
        return cls(
            incident_id=d["incident_id"],
            clusters=clusters,
            fault_class=FaultClass(fc) if fc else None,
            abstained=bool(d.get("abstained", False)),
            abstention_reason=d.get("abstention_reason"),
        )
