"""Contract shapes C1–C5 for the RCA engine.

C1/C2/C5 are upstream-facing stubs — keep interfaces swappable when Dev A
lands real adapters. C3/C4 are owned by Dev B.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class NodeKind(str, Enum):
    SERVICE = "service"
    INFRA = "infra"
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


# ---------------------------------------------------------------------------
# C2 — AnomalyOutput
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyOutput:
    """Per-node anomaly annotations for one incident window.

    FirstDeviationTime is seconds from incident t0 (or None for no-deviation /
    slow-burn nodes that never crossed the fire threshold).
    """

    anomaly_score: dict[str, float]  # node_id -> [0, 1]
    first_deviation_time: dict[str, Optional[float]]  # node_id -> seconds | None
    resource_deviation: dict[str, bool]  # node_id -> bool
    anomalous_trace_ids: frozenset[str] = frozenset()
    t0: float = 0.0  # incident reference time (seconds)


# ---------------------------------------------------------------------------
# C3 — RootCauseCandidate
# ---------------------------------------------------------------------------


@dataclass
class RootCauseCandidate:
    node_id: str
    score: float
    T: float
    D: float
    P: float
    role: Role
    dev_vector: dict[str, float]  # e.g. {"anomaly": ..., "T": ..., "D": ..., "P": ...}
    explanation: str
    cluster_id: Optional[str] = None
    # Flags for neutrals / diagnostics (not part of ranking contract, but useful).
    flags: dict[str, bool] = field(default_factory=dict)


@dataclass
class RankedResult:
    candidates: list[RootCauseCandidate]
    abstain_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# C5 — GroundTruth (isolation eval only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroundTruth:
    expected_cluster_count: int
    origin_nodes: frozenset[str]
    ambiguous: bool = False
