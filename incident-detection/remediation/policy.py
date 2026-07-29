"""Policy checks before propose/approve/execute."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from remediation.plan_schema import TypedPlanSpec
from runtime import profile as runtime_profile


def _truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default) in ("1", "true", "True", "yes", "YES")


def allowlist() -> set[str]:
    vals = runtime_profile.service_allowlist()
    return set(vals)


def allowed_namespaces() -> set[str]:
    return set(runtime_profile.allowed_namespaces())


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = ""
    code: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason, "code": self.code}


def kill_switch_active(*, namespace: str | None = None, service: str | None = None) -> PolicyDecision:
    if _truthy("CAUSA_REMEDIATION_KILL_SWITCH"):
        return PolicyDecision(False, "global kill switch CAUSA_REMEDIATION_KILL_SWITCH=1", "kill_switch_global")
    blocked_ns = {x.strip() for x in os.getenv("CAUSA_REMEDIATION_KILL_NS", "").split(",") if x.strip()}
    if namespace and namespace in blocked_ns:
        return PolicyDecision(False, f"namespace kill switch: {namespace}", "kill_switch_ns")
    blocked_svc = {x.strip() for x in os.getenv("CAUSA_REMEDIATION_KILL_SERVICE", "").split(",") if x.strip()}
    if service and service in blocked_svc:
        return PolicyDecision(False, f"service kill switch: {service}", "kill_switch_service")
    return PolicyDecision(True)


def check_plan_executable(spec: TypedPlanSpec, *, min_confidence: float | None = None, for_auto: bool = False) -> PolicyDecision:
    ks = kill_switch_active(namespace=spec.target.namespace, service=spec.candidate_node)
    if not ks.allowed:
        return ks
    if spec.is_expired():
        return PolicyDecision(False, f"plan expired at {spec.expires_at}", "expired")
    if spec.target.namespace not in allowed_namespaces():
        return PolicyDecision(False, f"namespace {spec.target.namespace} not allowed", "namespace_denied")
    node = spec.candidate_node or spec.target.name
    svc_allow = allowlist()
    if svc_allow and node not in svc_allow and spec.target.name not in svc_allow and spec.target.name != runtime_profile.configmap_name():
        if spec.action == "config_patch" and spec.target.name == runtime_profile.configmap_name():
            pass
        else:
            return PolicyDecision(False, f"{node} not in allowlist", "allowlist_denied")
    floor = min_confidence
    if floor is None:
        floor = runtime_profile.min_confidence_floor()
    if spec.confidence < floor and spec.risk_tier != "low":
        return PolicyDecision(False, f"confidence {spec.confidence:.3f} below floor {floor}", "confidence_low")
    if spec.action in ("scale_restart_cordon",) and not _truthy("CAUSA_REMEDIATION_ALLOW_INFRA"):
        return PolicyDecision(False, "infra actions require CAUSA_REMEDIATION_ALLOW_INFRA=1", "infra_blocked")
    if spec.action == "alert_only":
        return PolicyDecision(False, "alert_only plans are not executable", "stub_only")
    if not runtime_profile.supports_action(node, spec.action):
        return PolicyDecision(False, f"action {spec.action} not supported for {node}", "capability_denied")
    if for_auto:
        from remediation.calibration_gate import automation_calibrated

        local_demo_override = (
            _truthy("CAUSA_DEMO_ALLOW_UNCALIBRATED_AUTO_APPLY")
            and os.getenv("CAUSA_ENVIRONMENT") == "local-kind"
            and runtime_profile.load_profile().name == "otel-demo"
            and spec.target.namespace == "otel-demo"
            and spec.action in {"config_patch", "scale_up"}
        )
        if not automation_calibrated() and not local_demo_override:
            return PolicyDecision(
                False,
                "automatic remediation requires a validated calibration artifact",
                "uncalibrated",
            )
        auto_set = runtime_profile.action_auto_allowlist()
        if spec.action not in auto_set:
            return PolicyDecision(False, f"auto-apply not permitted for {spec.action}", "auto_denied")
    return PolicyDecision(True)


def check_approve_identity(authorization: str | None) -> PolicyDecision:
    required = os.getenv("REMEDIATION_APPROVAL_TOKEN", "").strip()
    if not required:
        return PolicyDecision(
            False,
            "REMEDIATION_APPROVAL_TOKEN not configured",
            "auth_misconfigured",
        )
    if not authorization:
        return PolicyDecision(False, "missing Authorization bearer token", "auth_missing")
    parts = authorization.split(None, 1)
    token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else authorization.strip()
    if token != required:
        return PolicyDecision(False, "invalid approval token", "auth_invalid")
    return PolicyDecision(True)


def risk_tier_for(action: str, confidence: float) -> tuple[str, int]:
    if action in ("code_fix_pr",):
        return "high", 1
    if action in ("rollback_to_last_good", "scale_up", "scale_restart_cordon", "restart_data_path"):
        return "medium", 1
    if action == "config_patch":
        return ("low" if confidence >= 0.5 else "medium"), 1
    return "medium", 1
