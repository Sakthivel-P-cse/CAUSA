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

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


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

    APP_LOGIC  = "AppLogic"
    CONFIG     = "Config"
    INFRA      = "Infra"
    DEPENDENCY = "Dependency"
    RESOURCE   = "Resource"
    DATA       = "Data"
    TIMING     = "Timing"   # S3 clock-skew; excluded from classification grading


@dataclass(frozen=True)
class Incident:
    """
    Ground truth for one incident (one chaos run).

      incident_id    — joins to the ingestion `incidents` row + the telemetry.
      true_root_node — the entity chaos targeted (service, DB, node, edge).
      fault_class    — one of FaultClass (stored as its string value).
      inject_start   — when the chaos fault began (aware UTC).
      inject_end     — when it was torn down (aware UTC).
    """

    incident_id: str
    true_root_node: str
    fault_class: str
    inject_start: datetime
    inject_end: datetime
