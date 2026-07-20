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
    T: float
    D: float
    P: float

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
    dev_vector: dict[str, float]
    explanation: str
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


@dataclass
class RankedResult:
    candidates: list[RootCauseCandidate]
    incident_id: str = ""
    abstain_reason: str | None = None
    fault_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "abstain_reason": self.abstain_reason,
            "fault_class": self.fault_class,
        }
