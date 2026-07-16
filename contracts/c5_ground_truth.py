"""C5 — GroundTruth (A → B, A → C).

Engine never sees these fields until output is compared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from contracts.c3_candidates import FaultClass


class ScenarioId(str, Enum):
    S0 = "S0"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"
    S5 = "S5"
    D1 = "D1"
    D2 = "D2"
    D3 = "D3"


@dataclass
class Incident:
    """Single-fault ground truth (spec §0)."""

    incident_id: str
    true_root_node: str
    fault_class: FaultClass
    inject_start: datetime
    inject_end: datetime
    scenario: ScenarioId | None = None
    injection_site: str | None = None  # for site-split generalization
    subgraph_node_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "true_root_node": self.true_root_node,
            "fault_class": self.fault_class.value,
            "inject_start": self.inject_start.isoformat(),
            "inject_end": self.inject_end.isoformat(),
            "scenario": self.scenario.value if self.scenario else None,
            "injection_site": self.injection_site,
            "subgraph_node_ids": list(self.subgraph_node_ids),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Incident:
        sc = d.get("scenario")
        return cls(
            incident_id=d["incident_id"],
            true_root_node=d["true_root_node"],
            fault_class=FaultClass(d["fault_class"]),
            inject_start=datetime.fromisoformat(d["inject_start"]),
            inject_end=datetime.fromisoformat(d["inject_end"]),
            scenario=ScenarioId(sc) if sc else None,
            injection_site=d.get("injection_site"),
            subgraph_node_ids=list(d.get("subgraph_node_ids") or []),
        )


@dataclass
class DualInjectionGroundTruth:
    """Dual-injection GT for D1–D3 isolation eval (ARI needs partition labels)."""

    incident_id: str
    true_root_nodes: list[str]
    fault_classes: list[FaultClass]
    # node_id -> fault index (0..k-1); required for Adjusted Rand Index
    node_to_fault: dict[str, int]
    inject_start: datetime
    inject_end: datetime
    scenario: ScenarioId
    expect_abstention: bool = False

    def expected_cluster_count(self) -> int:
        return len(self.true_root_nodes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "true_root_nodes": list(self.true_root_nodes),
            "fault_classes": [fc.value for fc in self.fault_classes],
            "node_to_fault": dict(self.node_to_fault),
            "inject_start": self.inject_start.isoformat(),
            "inject_end": self.inject_end.isoformat(),
            "scenario": self.scenario.value,
            "expect_abstention": self.expect_abstention,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DualInjectionGroundTruth:
        return cls(
            incident_id=d["incident_id"],
            true_root_nodes=list(d["true_root_nodes"]),
            fault_classes=[FaultClass(x) for x in d["fault_classes"]],
            node_to_fault={k: int(v) for k, v in (d.get("node_to_fault") or {}).items()},
            inject_start=datetime.fromisoformat(d["inject_start"]),
            inject_end=datetime.fromisoformat(d["inject_end"]),
            scenario=ScenarioId(d["scenario"]),
            expect_abstention=bool(d.get("expect_abstention", False)),
        )
