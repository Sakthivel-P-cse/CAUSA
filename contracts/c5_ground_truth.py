"""C5 — GroundTruth (A → B, A → C).

Core Incident fields match the shared seam. Optional eval extensions
(scenario, injection_site, subgraph_node_ids) stay on this type for the
Outputs lane without forcing Dev A producers to set them.

DualInjectionGroundTruth is an eval-facing label set for isolation metrics;
the engine never reads it.
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


def _parse_fault_class(raw: Any) -> FaultClass | str:
    if isinstance(raw, FaultClass):
        return raw
    try:
        return FaultClass(raw)
    except (ValueError, TypeError):
        return str(raw)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass
class Incident:
    """Single-fault ground truth (spec §0)."""

    incident_id: str
    true_root_node: str
    fault_class: FaultClass | str
    inject_start: datetime
    inject_end: datetime
    scenario: ScenarioId | None = None
    injection_site: str | None = None
    subgraph_node_ids: list[str] = field(default_factory=list)

    def fault_class_value(self) -> str:
        fc = self.fault_class
        return fc.value if isinstance(fc, FaultClass) else str(fc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "true_root_node": self.true_root_node,
            "fault_class": self.fault_class_value(),
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
            fault_class=_parse_fault_class(d["fault_class"]),
            inject_start=_parse_dt(d["inject_start"]),
            inject_end=_parse_dt(d["inject_end"]),
            scenario=ScenarioId(sc) if sc else None,
            injection_site=d.get("injection_site"),
            subgraph_node_ids=list(d.get("subgraph_node_ids") or []),
        )


@dataclass
class DualInjectionGroundTruth:
    """Eval-only dual-injection labels for ARI / cluster-count (not engine input)."""

    incident_id: str
    true_root_nodes: list[str]
    fault_classes: list[FaultClass | str]
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
            "fault_classes": [
                fc.value if isinstance(fc, FaultClass) else str(fc)
                for fc in self.fault_classes
            ],
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
            fault_classes=[_parse_fault_class(x) for x in d["fault_classes"]],
            node_to_fault={k: int(v) for k, v in (d.get("node_to_fault") or {}).items()},
            inject_start=_parse_dt(d["inject_start"]),
            inject_end=_parse_dt(d["inject_end"]),
            scenario=ScenarioId(d["scenario"]),
            expect_abstention=bool(d.get("expect_abstention", False)),
        )
