"""Resource/infra/data remediation proposals with typed executable plans."""

from __future__ import annotations

import logging
import os
from typing import Any

from contracts import FaultClass, RootCauseCandidate
from remediation.plan_schema import ExactTarget, PlanChange, SLOGate, TypedPlanSpec, fingerprint_payload
from remediation.policy import risk_tier_for
from runtime import profile as runtime_profile

logger = logging.getLogger(__name__)

DEFAULT_NS = runtime_profile.remediation_namespace()
MAX_REPLICAS = int(os.getenv("REMEDIATION_MAX_REPLICAS", "5"))


def stub_action(
    fault_class: FaultClass,
    candidate: RootCauseCandidate,
    *,
    incident_id: str = "",
    confidence: float | None = None,
) -> dict[str, Any]:
    if fault_class == FaultClass.RESOURCE:
        return propose_resource_scale(candidate, incident_id=incident_id, confidence=confidence)
    if fault_class == FaultClass.INFRA:
        return propose_infra_recovery(candidate, incident_id=incident_id, confidence=confidence)
    if fault_class == FaultClass.DATA:
        return propose_data_recovery(candidate, incident_id=incident_id, confidence=confidence)
    table = {}
    return {
        "summary": table.get(fault_class, f"Non-executable stub for {fault_class.value}"),
        "implemented": False,
        "executable": False,
        "target": candidate.node_id,
        "evidence_trail": candidate.to_dict(),
    }


def propose_resource_scale(
    candidate: RootCauseCandidate,
    *,
    incident_id: str = "",
    confidence: float | None = None,
) -> dict[str, Any]:
    conf = float(confidence if confidence is not None else candidate.confidence or 0.0)
    ns = DEFAULT_NS
    from remediation.policy import allowlist

    allowed = allowlist()
    if allowed and candidate.node_id not in allowed:
        return {
            "summary": f"{candidate.node_id} is outside remediation allowlist — runbook only",
            "implemented": False,
            "executable": False,
            "evidence_trail": candidate.to_dict(),
        }
    try:
        from remediation.apply.k8s import read_workload_revision

        live = read_workload_revision(candidate.node_id, ns)
        current = int(live.get("replicas") or 1)
        desired = min(MAX_REPLICAS, max(current + 1, current))
        if desired == current and current < MAX_REPLICAS:
            desired = current + 1
        if desired > MAX_REPLICAS:
            raise ValueError(f"desired replicas {desired} exceeds max {MAX_REPLICAS}")
        before_fp = fingerprint_payload(live)
        risk, approvals = risk_tier_for("scale_up", conf)
        resources = live.get("resources") or {}
        spec = TypedPlanSpec(
            action="scale_up",
            fault_class="Resource",
            target=ExactTarget(
                cluster=os.getenv("CONTEXT") or "kind-gcp-demo-test",
                namespace=ns,
                kind=str(live.get("kind") or "Deployment"),
                name=candidate.node_id,
                container=candidate.node_id,
            ),
            before_fingerprint=before_fp,
            change=PlanChange(
                description=f"Scale {candidate.node_id} {current} → {desired}",
                patch={"replicas": desired, "resources": resources},
                inverse={"replicas": current, "resources": resources},
            ),
            risk_tier=risk,
            required_approvals=approvals,
            slo_gates=SLOGate(require_fresh=False),
            incident_id=incident_id,
            candidate_node=candidate.node_id,
            confidence=conf,
            evidence=candidate.to_dict(),
        )
        return {
            "plan_spec": spec.to_dict(),
            "before_fingerprint": before_fp,
            "summary": f"Bounded scale-up {candidate.node_id}: {current}→{desired}",
            "solution": {
                "summary": f"Scale {candidate.node_id} from {current} to {desired} replicas",
                "path": f"{live.get('kind')}/{candidate.node_id}",
                "diff": f"- replicas: {current}\n+ replicas: {desired}\n",
            },
            "implemented": True,
            "executable": True,
            "evidence_trail": candidate.to_dict(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("resource propose failed: %s", exc)
        return {
            "summary": f"Cannot build resource plan: {exc}",
            "implemented": False,
            "executable": False,
            "error": str(exc),
            "evidence_trail": candidate.to_dict(),
        }


def propose_infra_recovery(
    candidate: RootCauseCandidate,
    *,
    incident_id: str = "",
    confidence: float | None = None,
) -> dict[str, Any]:
    conf = float(confidence if confidence is not None else candidate.confidence or 0.0)
    ns = DEFAULT_NS
    from remediation.policy import allowlist

    allowed = allowlist()
    if allowed and candidate.node_id not in allowed:
        return {
            "summary": f"{candidate.node_id} is outside remediation allowlist — runbook only",
            "implemented": False,
            "executable": False,
            "evidence_trail": candidate.to_dict(),
        }
    try:
        from remediation.apply.k8s import read_workload_revision

        live = read_workload_revision(candidate.node_id, ns)
        before_fp = fingerprint_payload(live)
        risk, approvals = risk_tier_for("scale_restart_cordon", conf)
        spec = TypedPlanSpec(
            action="scale_restart_cordon",
            fault_class="Infra",
            target=ExactTarget(
                cluster=os.getenv("CONTEXT") or "kind-gcp-demo-test",
                namespace=ns,
                kind=str(live.get("kind") or "Deployment"),
                name=candidate.node_id,
                container=candidate.node_id,
            ),
            before_fingerprint=before_fp,
            change=PlanChange(
                description=f"Restart workload for infra recovery: {candidate.node_id}",
                patch={"restart": True},
                inverse={"undo": True},
            ),
            risk_tier=risk,
            required_approvals=approvals,
            slo_gates=SLOGate(require_fresh=False),
            incident_id=incident_id,
            candidate_node=candidate.node_id,
            confidence=conf,
            evidence=candidate.to_dict(),
        )
        return {
            "plan_spec": spec.to_dict(),
            "before_fingerprint": before_fp,
            "summary": f"Infra recovery restart for {candidate.node_id}",
            "solution": {
                "summary": f"Restart {candidate.node_id} workload",
                "path": f"{live.get('kind')}/{candidate.node_id}",
                "diff": "+ restart rollout\n",
            },
            "implemented": True,
            "executable": True,
            "evidence_trail": candidate.to_dict(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("infra propose failed: %s", exc)
        return {
            "summary": f"Cannot build infra plan: {exc}",
            "implemented": False,
            "executable": False,
            "error": str(exc),
            "evidence_trail": candidate.to_dict(),
        }


def propose_data_recovery(
    candidate: RootCauseCandidate,
    *,
    incident_id: str = "",
    confidence: float | None = None,
) -> dict[str, Any]:
    conf = float(confidence if confidence is not None else candidate.confidence or 0.0)
    ns = DEFAULT_NS
    from remediation.policy import allowlist

    allowed = allowlist()
    if allowed and candidate.node_id not in allowed:
        return {
            "summary": f"{candidate.node_id} is outside remediation allowlist — runbook only",
            "implemented": False,
            "executable": False,
            "evidence_trail": candidate.to_dict(),
        }
    try:
        from remediation.apply.k8s import read_workload_revision

        live = read_workload_revision(candidate.node_id, ns)
        before_fp = fingerprint_payload(live)
        risk, approvals = risk_tier_for("restart_data_path", conf)
        spec = TypedPlanSpec(
            action="restart_data_path",
            fault_class="Data",
            target=ExactTarget(
                cluster=os.getenv("CONTEXT") or "kind-gcp-demo-test",
                namespace=ns,
                kind=str(live.get("kind") or "Deployment"),
                name=candidate.node_id,
                container=candidate.node_id,
            ),
            before_fingerprint=before_fp,
            change=PlanChange(
                description=f"Restart workload for data path recovery: {candidate.node_id}",
                patch={"restart": True},
                inverse={"undo": True},
            ),
            risk_tier=risk,
            required_approvals=approvals,
            slo_gates=SLOGate(require_fresh=False),
            incident_id=incident_id,
            candidate_node=candidate.node_id,
            confidence=conf,
            evidence=candidate.to_dict(),
        )
        return {
            "plan_spec": spec.to_dict(),
            "before_fingerprint": before_fp,
            "summary": f"Data-path recovery restart for {candidate.node_id}",
            "solution": {
                "summary": f"Restart {candidate.node_id} workload",
                "path": f"{live.get('kind')}/{candidate.node_id}",
                "diff": "+ restart rollout\n",
            },
            "implemented": True,
            "executable": True,
            "evidence_trail": candidate.to_dict(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("data propose failed: %s", exc)
        return {
            "summary": f"Cannot build data plan: {exc}",
            "implemented": False,
            "executable": False,
            "error": str(exc),
            "evidence_trail": candidate.to_dict(),
        }
