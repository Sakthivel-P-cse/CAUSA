"""Route fault_class → remediation (spec §6.1: 3 deep, rest stubs)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contracts.c3_candidates import FaultClass, RankedResult, RootCauseCandidate
from remediation.branches.applogic import propose_code_fix_pr
from remediation.branches.config import propose_config_patch
from remediation.branches.dependency import propose_rollback
from remediation.branches.stubs import stub_action


@dataclass
class RemediationPlan:
    fault_class: FaultClass
    action: str
    depth: str  # "deep" | "stub"
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    candidate: RootCauseCandidate | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fault_class": self.fault_class.value,
            "action": self.action,
            "depth": self.depth,
            "summary": self.summary,
            "details": self.details,
            "candidate_node": self.candidate.node_id if self.candidate else None,
        }


class RemediationRouter:
    def route(
        self,
        fault_class: FaultClass,
        candidate: RootCauseCandidate,
        ranked: RankedResult | None = None,
    ) -> RemediationPlan:
        if fault_class == FaultClass.APP_LOGIC:
            details = propose_code_fix_pr(candidate, ranked)
            return RemediationPlan(
                fault_class=fault_class,
                action="code_fix_pr",
                depth="deep",
                summary=f"Open proposed PR for {candidate.node_id} (never auto-merged)",
                details=details,
                candidate=candidate,
            )
        if fault_class == FaultClass.CONFIG:
            details = propose_config_patch(candidate)
            return RemediationPlan(
                fault_class=fault_class,
                action="config_patch",
                depth="deep",
                summary=f"Propose config patch for {candidate.node_id}",
                details=details,
                candidate=candidate,
            )
        if fault_class == FaultClass.DEPENDENCY:
            details = propose_rollback(candidate)
            return RemediationPlan(
                fault_class=fault_class,
                action="rollback_to_last_good",
                depth="deep",
                summary=f"Rollback / circuit-break dependency {candidate.node_id}",
                details=details,
                candidate=candidate,
            )
        # Stubs
        details = stub_action(fault_class, candidate)
        action = {
            FaultClass.INFRA: "scale_restart_cordon",
            FaultClass.RESOURCE: "scale_up",
            FaultClass.DATA: "alert_only",
        }[fault_class]
        return RemediationPlan(
            fault_class=fault_class,
            action=action,
            depth="stub",
            summary=details["summary"],
            details=details,
            candidate=candidate,
        )
