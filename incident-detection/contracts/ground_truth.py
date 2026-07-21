"""
contracts/ground_truth.py — Causa contract C5: chaos ground truth.

One record per chaos experiment run (RCA_Engine_Spec §0). The chaos CRD IS the
ground truth — it is never hand-authored to make a scenario "work" (PRD2 §6
anti-circularity). The testbed harness writes these OUT-OF-BAND.

INVARIANT: the RCA engine must NEVER read a GroundTruth row before its output is
scored against it. This is enforced at the Postgres role/grant level (the engine
role has no SELECT on the groundtruth schema), not by convention.

stdlib-only. Consumed by Dev C (accuracy) and Dev B (isolation eval).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class FaultClass(str, Enum):
    """
    The six failure classes (RCA_Engine_Spec §0/§6.4) — Dev C's classification
    target — plus TIMING for clock-skew ground truth.

    TIMING (S3 TimeChaos) is deliberately NOT one of the six classification
    targets: the scenario attacks ordering/causality, not a fault category, and
    the CRD header calls it "not a class target." It is carried so S3's
    GroundTruth is honest (localizing the faulted node) without mislabeling a
    timing fault as a Dependency fault, and Dev C excludes it from classifier
    grading.
    """

    APP_LOGIC = "AppLogic"
    CONFIG = "Config"
    INFRA = "Infra"
    DEPENDENCY = "Dependency"
    RESOURCE = "Resource"
    DATA = "Data"
    TIMING = "Timing"


CLASSIFICATION_TARGETS: tuple[FaultClass, ...] = tuple(
    fc for fc in FaultClass if fc is not FaultClass.TIMING
)


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


@dataclass(frozen=True)
class Incident:
    """
    Ground truth for one incident (one chaos run).

      incident_id    — joins to the ingestion `incidents` row + the telemetry.
      true_root_node — the entity chaos targeted (service, DB, node, edge).
      fault_class    — one of FaultClass (stored as its string value or enum).
      inject_start   — when the chaos fault began (aware UTC).
      inject_end     — when it was torn down (aware UTC).

    Optional eval extensions (scenario / injection_site / subgraph_node_ids)
    stay on this type for the Outputs lane; Dev A producers need not set them.
    """

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
            "inject_start": self.inject_start.isoformat()
            if isinstance(self.inject_start, datetime)
            else str(self.inject_start),
            "inject_end": self.inject_end.isoformat()
            if isinstance(self.inject_end, datetime)
            else str(self.inject_end),
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


@dataclass(frozen=True)
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
            "inject_start": self.inject_start.isoformat()
            if isinstance(self.inject_start, datetime)
            else str(self.inject_start),
            "inject_end": self.inject_end.isoformat()
            if isinstance(self.inject_end, datetime)
            else str(self.inject_end),
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
