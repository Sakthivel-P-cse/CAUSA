"""Dependency deep branch — rollback to recorded last-good revision."""

from __future__ import annotations

import logging
import os
from typing import Any

from contracts import RootCauseCandidate
from remediation.plan_schema import ExactTarget, PlanChange, SLOGate, TypedPlanSpec, fingerprint_payload
from remediation.policy import risk_tier_for
from runtime import profile as runtime_profile

logger = logging.getLogger(__name__)

DEFAULT_NS = runtime_profile.remediation_namespace()


def propose_rollback(candidate: RootCauseCandidate, *, incident_id: str = "", confidence: float | None = None) -> dict[str, Any]:
    ns = DEFAULT_NS
    conf = float(confidence if confidence is not None else candidate.confidence or 0.0)
    try:
        from remediation.apply.k8s import read_workload_revision

        live = read_workload_revision(candidate.node_id, ns)
        prior_rev = live.get("revision")
        if not prior_rev and live.get("kind") != "Rollout":
            raise ValueError(f"no prior revision found for {candidate.node_id}")
        before_fp = fingerprint_payload(live)
        risk, approvals = risk_tier_for("rollback_to_last_good", conf)
        target_kind = "Rollout" if live.get("kind") == "Rollout" else "Deployment"
        spec = TypedPlanSpec(
            action="rollback_to_last_good",
            fault_class="Dependency",
            target=ExactTarget(
                cluster=os.getenv("CONTEXT") or "kind-gcp-demo-test",
                namespace=ns,
                kind=target_kind,
                name=candidate.node_id,
                revision=str(prior_rev) if prior_rev else None,
            ),
            before_fingerprint=before_fp,
            change=PlanChange(
                description=f"Undo {candidate.node_id} to revision {prior_rev}",
                patch={"revision": prior_rev, "images": live.get("images")},
                inverse={
                    "revision": "current",
                    "images": live.get("images"),
                    "note": "re-apply current images if undo must be reversed",
                },
            ),
            risk_tier=risk,
            required_approvals=approvals,
            slo_gates=SLOGate(),
            incident_id=incident_id,
            candidate_node=candidate.node_id,
            confidence=conf,
            evidence=candidate.to_dict(),
        )
        return {
            "plan_spec": spec.to_dict(),
            "before_fingerprint": before_fp,
            "rollback": {
                "target": candidate.node_id,
                "strategy": "rollback_to_last_good",
                "last_good_revision": prior_rev,
                "kind": target_kind,
                "steps": [
                    f"Verify before-fingerprint {before_fp[:12]}",
                    f"Undo {target_kind}/{candidate.node_id} to revision {prior_rev}",
                    "Observe Prometheus SLO gates via Argo Analysis / live queries",
                ],
            },
            "evidence_trail": candidate.to_dict(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("dependency propose failed: %s", exc)
        return {
            "executable": False,
            "error": str(exc),
            "evidence_trail": candidate.to_dict(),
            "summary": f"Cannot build revision rollback plan: {exc}",
        }
