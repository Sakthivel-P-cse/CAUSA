"""Causa contracts C3/C4: serializable RCA ranking output types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeKind(str, Enum):
    """What a graph node IS, defined structurally so the classification is
    derivable from telemetry rather than from naming convention.

    Each kind is separated by an observable property, not by intuition about the
    name — `cart-db` looking like a database is not evidence:

      SERVICE    — an instrumented workload: it emits spans under its own
                   service name, and/or originates calls to others.
      DEPENDENCY — a backing store, queue, or external endpoint that is CALLED
                   but is not instrumented: it emits no spans of its own and
                   originates no calls (a pure sink in the call graph).
      INFRA      — a host: workloads run ON it. Structurally, the source of a
                   runs-on edge. NOT "anything that isn't a service".
      SPAN       — a span vertex in the trace execution graph.

    INFRA and DEPENDENCY were previously conflated, with INFRA used loosely for
    "not a service" (a DB was typed INFRA in some fixtures and DEPENDENCY in
    others, and `adapt_incident` emitted only INFRA/SERVICE so DEPENDENCY never
    appeared on live data at all). They are distinct causal layers: a saturated
    HOST explains every workload on it, while a degraded DEPENDENCY explains
    only its callers. Layer arbitration is meaningful for the first and wrong
    for the second, so the distinction has to survive into the graph.
    """

    SERVICE = "service"
    INFRA = "infra"
    DEPENDENCY = "dependency"
    SPAN = "span"


class Role(str, Enum):
    ORIGIN = "origin"
    AMPLIFIER = "amplifier"
    VICTIM = "victim"
    VICTIM_OF_INFRA = "victim-of-infra"
    UNKNOWN = "unknown"


@dataclass
class SignalScores:
    T: float = 0.0
    D: float = 0.0
    P: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {"T": self.T, "D": self.D, "P": self.P}


@dataclass
class RootCauseCandidate:
    node_id: str
    node_kind: NodeKind
    score: float
    confidence: float
    role: Role
    signals: SignalScores
    dev_vector: dict[str, float] = field(default_factory=dict)
    explanation: str = ""
    cluster_id: str | None = None
    flags: dict[str, bool] = field(default_factory=dict)

    @property
    def T(self) -> float:
        return self.signals.T

    @property
    def D(self) -> float:
        return self.signals.D

    @property
    def P(self) -> float:
        return self.signals.P

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_kind": self.node_kind.value,
            "score": self.score,
            "confidence": self.confidence,
            "role": self.role.value,
            "signals": self.signals.to_dict(),
            "dev_vector": dict(self.dev_vector),
            "explanation": self.explanation,
            "cluster_id": self.cluster_id,
            "flags": dict(self.flags),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RootCauseCandidate:
        sig = d.get("signals") or {}
        return cls(
            node_id=d["node_id"],
            node_kind=NodeKind(d["node_kind"]),
            score=float(d["score"]),
            confidence=float(d.get("confidence", 0.0)),
            role=Role(d["role"]),
            signals=SignalScores(
                T=float(sig.get("T", 0.0)),
                D=float(sig.get("D", 0.0)),
                P=float(sig.get("P", 0.0)),
            ),
            dev_vector=dict(d.get("dev_vector") or {}),
            explanation=str(d.get("explanation") or ""),
            cluster_id=d.get("cluster_id"),
            flags=dict(d.get("flags") or {}),
        )


ABSTENTION_FLAG = "MULTI-INCIDENT — ambiguous"


@dataclass
class RankedResult:
    candidates: list[RootCauseCandidate]
    incident_id: str = ""
    abstain_reason: str | None = None
    fault_class: Any = None

    @property
    def abstained(self) -> bool:
        return self.abstain_reason is not None

    @property
    def primary_candidates(self) -> list[RootCauseCandidate]:
        return self.candidates

    def ranked_node_ids(self, cluster_idx: int = 0) -> list[str]:
        if cluster_idx != 0:
            return []
        return [c.node_id for c in self.candidates]

    def to_dict(self) -> dict[str, Any]:
        fc = self.fault_class
        if hasattr(fc, "value"):
            fc = fc.value
        return {
            "incident_id": self.incident_id,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "abstain_reason": self.abstain_reason,
            "fault_class": fc,
            "abstained": self.abstained,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RankedResult:
        candidates: list[RootCauseCandidate] = []
        if d.get("candidates"):
            candidates = [RootCauseCandidate.from_dict(c) for c in d["candidates"]]
        elif d.get("clusters"):
            merged: list[RootCauseCandidate] = []
            for cl in d["clusters"]:
                for c in cl.get("candidates") or []:
                    cand = RootCauseCandidate.from_dict(c)
                    if cand.cluster_id is None and cl.get("fault_id") is not None:
                        cand.cluster_id = str(cl["fault_id"])
                    merged.append(cand)
            candidates = merged

        fc_raw = d.get("fault_class")
        fault_class: Any
        if fc_raw is None:
            fault_class = None
        else:
            try:
                from contracts.ground_truth import FaultClass as _FC

                fault_class = _FC(fc_raw)
            except (ValueError, TypeError):
                fault_class = str(fc_raw.value if hasattr(fc_raw, "value") else fc_raw)

        abstain = d.get("abstain_reason")
        if abstain is None and d.get("abstained"):
            abstain = d.get("abstention_reason") or ABSTENTION_FLAG

        return cls(
            candidates=candidates,
            incident_id=str(d.get("incident_id") or ""),
            fault_class=fault_class,
            abstain_reason=abstain,
        )


@dataclass
class RankedCluster:
    """Legacy helper for tests; prefer IsolationView for multi-fault eval."""

    fault_id: str | None
    candidates: list[RootCauseCandidate] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)


NodeRole = Role

