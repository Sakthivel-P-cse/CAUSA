"""C2 — Anomaly primitives (A → B; C uses scores for MostAnomalous baseline).

Canonical shape matches the shared seam: one AnomalyOutput per node.
Eval may aggregate nodes into AnomalyBag for ranking helpers without depending
on Dev A's anomaly package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping


@dataclass(frozen=True)
class Thresholds:
    theta_fire: float = 0.5
    k_persist: int = 3
    theta_low: float = 0.25
    kappa: float = 3.0


AnomalyThresholds = Thresholds


@dataclass
class AnomalyOutput:
    """Per-node anomaly summary (shared C2)."""

    node: str
    anomaly_score: float
    dev_vector: dict[str, float] = field(default_factory=dict)
    first_deviation: datetime | None = None
    window_ts: datetime | None = None

    @property
    def node_id(self) -> str:
        return self.node


@dataclass
class AnomalyBag:
    """Incident-scoped score lookup for eval baselines (not a cross-lane contract)."""

    incident_id: str
    nodes: dict[str, AnomalyOutput]
    thresholds: Thresholds = field(default_factory=Thresholds)

    def score(self, node_id: str) -> float:
        n = self.nodes.get(node_id)
        return n.anomaly_score if n else 0.0

    def dev_vector(self, node_id: str) -> dict[str, float]:
        n = self.nodes.get(node_id)
        return dict(n.dev_vector) if n else {}

    @classmethod
    def from_outputs(
        cls,
        incident_id: str,
        outputs: Iterable[AnomalyOutput],
        thresholds: Thresholds | None = None,
    ) -> AnomalyBag:
        nodes = {o.node: o for o in outputs}
        return cls(
            incident_id=incident_id,
            nodes=nodes,
            thresholds=thresholds or Thresholds(),
        )

    @classmethod
    def from_score_map(
        cls,
        incident_id: str,
        scores: Mapping[str, float],
        thresholds: Thresholds | None = None,
    ) -> AnomalyBag:
        nodes = {
            nid: AnomalyOutput(node=nid, anomaly_score=float(s))
            for nid, s in scores.items()
        }
        return cls(
            incident_id=incident_id,
            nodes=nodes,
            thresholds=thresholds or Thresholds(),
        )


NodeAnomaly = AnomalyOutput
