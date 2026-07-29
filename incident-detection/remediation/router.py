"""Route fault_class → remediation (typed plan_spec for executable actions)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contracts import FaultClass, RankedResult, RootCauseCandidate
from remediation.branches.applogic import propose_code_fix_pr
from remediation.branches.config import propose_config_patch
from remediation.branches.dependency import propose_rollback
from remediation.branches.stubs import stub_action
from runtime import profile as runtime_profile


@dataclass
class RemediationPlan:
    fault_class: FaultClass
    action: str
    depth: str
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
        *,
        incident_id: str = "",
    ) -> RemediationPlan:
        iid = incident_id or (ranked.incident_id if ranked else "")
        if fault_class == FaultClass.APP_LOGIC:
            if not runtime_profile.supports_action(candidate.node_id, "code_fix_pr"):
                return RemediationPlan(
                    fault_class=fault_class,
                    action="code_fix_pr",
                    depth="stub",
                    summary=f"AppLogic remediation not supported for {candidate.node_id} by profile",
                    details={"executable": False, "reason": "capability_denied"},
                    candidate=candidate,
                )
            details = propose_code_fix_pr(candidate, ranked, incident_id=iid)
            return RemediationPlan(
                fault_class=fault_class,
                action="code_fix_pr",
                depth="deep",
                summary=f"Draft code-fix PR for {candidate.node_id} (never auto-merged)",
                details=details,
                candidate=candidate,
            )
        if fault_class == FaultClass.CONFIG:
            if not runtime_profile.supports_action(candidate.node_id, "config_patch"):
                return RemediationPlan(
                    fault_class=fault_class,
                    action="config_patch",
                    depth="stub",
                    summary=f"Config remediation not supported for {candidate.node_id} by profile",
                    details={"executable": False, "reason": "capability_denied"},
                    candidate=candidate,
                )
            details = propose_config_patch(candidate, incident_id=iid)
            executable = "plan_spec" in details and details.get("executable", True) is not False
            return RemediationPlan(
                fault_class=fault_class,
                action="config_patch",
                depth="deep" if executable else "stub",
                summary=details.get("summary") or f"Exact config patch for {candidate.node_id}",
                details=details,
                candidate=candidate,
            )
        if fault_class == FaultClass.DEPENDENCY:
            if not runtime_profile.supports_action(candidate.node_id, "rollback_to_last_good"):
                return RemediationPlan(
                    fault_class=fault_class,
                    action="rollback_to_last_good",
                    depth="stub",
                    summary=f"Dependency remediation not supported for {candidate.node_id} by profile",
                    details={"executable": False, "reason": "capability_denied"},
                    candidate=candidate,
                )
            details = propose_rollback(candidate, incident_id=iid)
            executable = "plan_spec" in details
            return RemediationPlan(
                fault_class=fault_class,
                action="rollback_to_last_good",
                depth="deep" if executable else "stub",
                summary=details.get("summary") or f"Rollback {candidate.node_id} to last-good revision",
                details=details,
                candidate=candidate,
            )
        action = {
            FaultClass.INFRA: "scale_restart_cordon",
            FaultClass.RESOURCE: "scale_up",
            FaultClass.DATA: "restart_data_path",
        }.get(fault_class, "alert_only")
        if not runtime_profile.supports_action(candidate.node_id, action):
            return RemediationPlan(
                fault_class=fault_class,
                action=action,
                depth="stub",
                summary=f"{fault_class.value} remediation not supported for {candidate.node_id} by profile",
                details={"executable": False, "reason": "capability_denied"},
                candidate=candidate,
            )
        details = stub_action(fault_class, candidate, incident_id=iid)
        depth = "deep" if details.get("executable") else "stub"
        return RemediationPlan(
            fault_class=fault_class,
            action=action,
            depth=depth,
            summary=details.get("summary") or f"Stub remediation for {fault_class.value}",
            details=details,
            candidate=candidate,
        )
