"""Auto-propose (and optionally auto-approve) remediation after RCA."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default) in ("1", "true", "True", "yes", "YES")


def auto_remediate_from_report(report: dict[str, Any]) -> dict[str, Any] | None:
    """Create a remediation plan for an RCA report; auto-approve when enabled."""
    if _truthy("CAUSA_REMEDIATION_KILL_SWITCH"):
        return {"skipped": True, "reason": "kill_switch"}
    if not _truthy("CAUSA_REMEDIATION_AUTO_PROPOSE", "0"):
        return {"skipped": True, "reason": "auto_propose_disabled"}

    incident_id = str(report.get("incident_id") or report.get("id") or "")
    if not incident_id:
        return {"skipped": True, "reason": "no_incident_id"}

    try:
        from remediation.service import propose_for_report
        from dashboard.backend.remediation_store import RemediationStore
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto remediate imports failed: %s", exc)
        return {"skipped": True, "reason": str(exc)}

    result = propose_for_report(report)
    if not result.get("ok") or not result.get("plan"):
        return {"skipped": True, "reason": result.get("error") or "propose_failed"}

    store = RemediationStore()
    existing = store.list_plans(incident_id=incident_id, limit=5)
    for row in existing:
        status = row.get("status")
        has_spec = bool(row.get("plan_spec") or (row.get("details") or {}).get("plan_spec"))
        if status in ("approved", "claimed", "executing", "observing", "succeeded"):
            return {"skipped": True, "reason": "plan_already_exists", "plan_id": row.get("id")}
        if status == "proposed" and has_spec:
            if _truthy("CAUSA_REMEDIATION_AUTO_APPLY", "0"):
                from remediation.plan_schema import TypedPlanSpec
                from remediation.policy import check_plan_executable

                try:
                    spec = TypedPlanSpec.from_dict(row.get("plan_spec") or (row.get("details") or {}).get("plan_spec"))
                    if check_plan_executable(spec, for_auto=True).allowed:
                        store.set_status(row["id"], "approved", decided_by="auto")
                        return {"ok": True, "plan_id": row["id"], "status": "approved", "auto_approved": True}
                except Exception:
                    pass
            return {"skipped": True, "reason": "plan_already_exists", "plan_id": row.get("id")}
        # A plan rejected before any mutation is safe to re-evaluate after a
        # policy/configuration correction. Never auto-retry an attempted
        # mutation: the idempotency key remains the barrier for that case.
        apply_result = row.get("apply_result") or {}
        if (
            status == "failed"
            and has_spec
            and isinstance(apply_result, dict)
            and apply_result.get("attempted") is False
            and _truthy("CAUSA_REMEDIATION_AUTO_APPLY", "0")
        ):
            from remediation.plan_schema import TypedPlanSpec
            from remediation.policy import check_plan_executable

            try:
                spec = TypedPlanSpec.from_dict(
                    row.get("plan_spec") or (row.get("details") or {}).get("plan_spec")
                )
                decision = check_plan_executable(spec, for_auto=True)
                if decision.allowed:
                    store.set_status(row["id"], "approved", decided_by="auto")
                    store.add_event(
                        row["id"],
                        "auto_retry_approved",
                        {"policy": decision.to_dict(), "previous_attempted": False},
                    )
                    return {
                        "ok": True,
                        "plan_id": row["id"],
                        "status": "approved",
                        "auto_approved": True,
                        "retry": True,
                    }
            except Exception as exc:  # noqa: BLE001
                logger.warning("safe auto-retry failed: %s", exc)

    saved = store.upsert_proposed(result["plan"])
    plan_spec = saved.get("plan_spec") or (saved.get("details") or {}).get("plan_spec")
    auto_apply = _truthy("CAUSA_REMEDIATION_AUTO_APPLY", "0")
    if auto_apply and isinstance(plan_spec, dict) and saved.get("depth") != "stub":
        from remediation.plan_schema import TypedPlanSpec
        from remediation.policy import check_plan_executable

        try:
            spec = TypedPlanSpec.from_dict(plan_spec)
            decision = check_plan_executable(spec, for_auto=True)
            if decision.allowed:
                store.set_status(saved["id"], "approved", decided_by="auto")
                store.add_event(saved["id"], "auto_approved", {"policy": decision.to_dict()})
                return {"ok": True, "plan_id": saved["id"], "status": "approved", "auto_approved": True}
            store.add_event(saved["id"], "auto_approve_denied", decision.to_dict())
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto-approve failed: %s", exc)
            store.add_event(saved["id"], "auto_approve_error", {"error": str(exc)})
    elif auto_apply and not isinstance(plan_spec, dict):
        store.add_event(saved["id"], "auto_approve_skipped", {"reason": "no_plan_spec"})
    return {"ok": True, "plan_id": saved["id"], "status": saved.get("status"), "auto_approved": False}
