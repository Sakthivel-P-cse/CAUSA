"""C2 — AnomalyOutput (A → B; fixtures → C baselines).

MostAnomalous baseline sorts by anomaly_score without needing Dev B.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class AnomalyThresholds:
    theta_fire: float = 0.5
    k_persist: int = 3
    theta_low: float = 0.2


@dataclass
class NodeAnomaly:
    node_id: str
    anomaly_score: float  # [0, 1]
    dev_vector: dict[str, float] = field(default_factory=dict)
    first_deviation: datetime | None = None  # null = slow-burn


@dataclass
class AnomalyOutput:
    """Per-incident anomaly primitives for all subgraph nodes."""

    incident_id: str
    nodes: dict[str, NodeAnomaly]
    thresholds: AnomalyThresholds = field(default_factory=AnomalyThresholds)

    def score(self, node_id: str) -> float:
        n = self.nodes.get(node_id)
        return n.anomaly_score if n else 0.0
