"""Contract shapes C1–C5 for the RCA engine.

Aligned to TraceDoc ``Team_Split.md`` C1–C5 and ``RCA_Engine_Spec.md`` §2/§5.
C1/C2/C5 are upstream-facing stubs — swappable when Dev A lands real adapters.
C3/C4 are owned by Dev B.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


class NodeKind(str, Enum):
    SERVICE = "service"
    INFRA = "infra"
    DEPENDENCY = "dependency"
    SPAN = "span"


class CallType(str, Enum):
    SYNC = "sync"
    ASYNC = "async"
    RUNS_ON = "runs-on"


class Role(str, Enum):
    ORIGIN = "origin"
    AMPLIFIER = "amplifier"
    VICTIM = "victim"
    VICTIM_OF_INFRA = "victim-of-infra"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# C1 — topology + traces (stub / swappable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Node:
    node_id: str
    kind: NodeKind
    name: str
    # Optional host link for service nodes (infra node_id).
    runs_on: Optional[str] = None


@dataclass(frozen=True)
class Edge:
    """Directed edge in call / topology graph (caller → callee for sync)."""

    src: str
    dst: str
    call_type: CallType
    # Optional edge metric degradation flag (True if degraded in-window).
    degraded: bool = False


@dataclass(frozen=True)
class Span:
    span_id: str
    service_id: str
    parent_span_id: Optional[str]
    start_ms: float
    end_ms: float
    # Span kind / attributes used to derive call_type when building edges.
    span_kind: str = "CLIENT"  # CLIENT/SERVER/PRODUCER/CONSUMER/INTERNAL
    attributes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Trace:
    trace_id: str
    spans: tuple[Span, ...]
    anomalous: bool = False


@dataclass(frozen=True)
class Topology:
    """C1 topology() output."""

    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]


@runtime_checkable
class TelemetryAdapters(Protocol):
    """Thin C1 seam: Dev A swaps real OTel adapters behind this Protocol."""

    def topology(self) -> Topology:
        """Return infra + service edges for the current deployment."""
        ...

    def traces(self, window: tuple[float, float]) -> list[Trace]:
        """Return traces whose spans overlap ``window`` (start_s, end_s)."""
        ...


# ---------------------------------------------------------------------------
# C2 — AnomalyOutput
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyThresholds:
    """Fire / persistence / connectivity thresholds (spec §1.1)."""

    theta_fire: float = 0.5
    k_persist: int = 3
    theta_low: float = 0.2


@dataclass(frozen=True)
class AnomalyOutput:
    """Per-node anomaly annotations for one incident window.

    FirstDeviationTime is seconds from incident reference (or None for
    no-deviation / slow-burn nodes that never crossed the fire threshold).
    """

    anomaly_score: dict[str, float]  # node_id -> [0, 1]
    first_deviation_time: dict[str, Optional[float]]  # node_id -> seconds | None
    resource_deviation: dict[str, bool]  # node_id -> bool
    anomalous_trace_ids: frozenset[str] = frozenset()
    t0: float = 0.0  # incident reference time (seconds); scoring uses subgraph min FDT
    # Per-node metric deviations {node_id: {metric: float}} for explainability.
    dev_vector: dict[str, dict[str, float]] = field(default_factory=dict)
    thresholds: AnomalyThresholds = field(default_factory=AnomalyThresholds)


# ---------------------------------------------------------------------------
# C3 — RootCauseCandidate
# ---------------------------------------------------------------------------


@dataclass
class SignalScores:
    """Nested T/D/P sub-scores (TraceDoc C3 ``signals``)."""

    T: float
    D: float
    P: float


@dataclass
class RootCauseCandidate:
    node_id: str
    node_kind: NodeKind
    score: float
    confidence: float
    role: Role
    signals: SignalScores
    dev_vector: dict[str, float]  # per-metric deviations (from C2 when present)
    explanation: str
    cluster_id: Optional[str] = None
    # Flags for neutrals / diagnostics (not part of ranking contract, but useful).
    flags: dict[str, bool] = field(default_factory=dict)

    # Flat accessors — prefer ``signals`` for new code; kept for existing tests.
    @property
    def T(self) -> float:
        return self.signals.T

    @property
    def D(self) -> float:
        return self.signals.D

    @property
    def P(self) -> float:
        return self.signals.P


@dataclass
class RankedResult:
    candidates: list[RootCauseCandidate]
    incident_id: str = ""
    abstain_reason: Optional[str] = None
    fault_class: Optional[str] = None


# ---------------------------------------------------------------------------
# C5 — GroundTruth / Incident
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroundTruth:
    """C5 Incident ground truth (A → B/C).

    Core fields match TraceDoc ``Incident``. Isolation-eval helpers
    (``expected_cluster_count``, ``origin_nodes``, ``ambiguous``) are optional
    derivatives for multi-fault DoD checks — not consumed by the scorer.
    """

    incident_id: str
    true_root_node: str
    fault_class: str
    inject_start: float
    inject_end: float
    expected_cluster_count: int = 1
    origin_nodes: frozenset[str] = field(default_factory=frozenset)
    ambiguous: bool = False
