from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from dashboard.backend.dependencies import get_store
from dashboard.backend.db import DashboardStore
from dashboard.backend.errors import api_error
from dashboard.backend.remediation_store import APPROVABLE, RemediationStore

_ID = Path(__file__).resolve().parents[3] / "incident-detection"
if str(_ID) not in sys.path:
    sys.path.insert(0, str(_ID))

router = APIRouter()
_rem_store: RemediationStore | None = None
def get_rem_store() -> RemediationStore:
    global _rem_store
    if _rem_store is None:
        _rem_store = RemediationStore()
    return _rem_store


class ProposeBody(BaseModel):
    incident_id: str = Field(..., min_length=1)
    manual_prompt: str | None = Field(default=None, max_length=4096)
    prompt_mode: Literal["append", "constraints", "replace"] = "constraints"
    regen_of_plan_id: str | None = None

    @field_validator("manual_prompt")
    @classmethod
    def _strip_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class RegenerateBody(BaseModel):
    manual_prompt: str | None = Field(default=None, max_length=4096)
    prompt_mode: Literal["append", "constraints", "replace"] = "constraints"
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("manual_prompt")
    @classmethod
    def _strip_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class DecisionBody(BaseModel):
    decided_by: str | None = "operator"


def _find_report(store: DashboardStore, incident_id: str) -> dict[str, Any] | None:
    item = store.get_report(incident_id)
    if item:
        return item
    for row in store.list_incidents_joined():
        rid = str(row.get("id") or "")
        iid = str(row.get("incident_id") or "")
        if incident_id in (rid, iid):
            detailed = store.get_report(rid or iid)
            return detailed or row
    return None


@router.get("/nim/health")
def remediation_nim_health() -> dict[str, Any]:
    from remediation.nim_client import nim_health

    return nim_health()


@router.get("/stats")
def remediation_stats(rem: RemediationStore = Depends(get_rem_store)) -> dict[str, Any]:
    return rem.stats()


@router.get("/plans")
def list_plans(
    status: str | None = None,
    fault_class: str | None = None,
    incident_id: str | None = None,
    limit: int = 100,
    rem: RemediationStore = Depends(get_rem_store),
) -> dict[str, Any]:
    items = rem.list_plans(
        status=status,
        fault_class=fault_class,
        incident_id=incident_id,
        limit=min(limit, 500),
    )
    return {"items": items, "total": len(items)}


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, rem: RemediationStore = Depends(get_rem_store)) -> dict[str, Any]:
    row = rem.get(plan_id)
    if not row:
        raise api_error(404, "not_found", "Remediation plan not found")
    return row


@router.post("/ensure")
def ensure_plan(
    body: ProposeBody,
    store: DashboardStore = Depends(get_store),
    rem: RemediationStore = Depends(get_rem_store),
) -> dict[str, Any]:
    """Idempotent: auto-propose (+ auto-approve) if no active plan exists."""
    report = _find_report(store, body.incident_id)
    if not report:
        raise api_error(404, "not_found", f"Incident not found: {body.incident_id}")
    from remediation.auto import auto_remediate_from_report

    result = auto_remediate_from_report(report)
    items = rem.list_plans(incident_id=body.incident_id, limit=5)
    return {"result": result, "plans": items}


@router.post("/propose")
def propose(
    body: ProposeBody,
    store: DashboardStore = Depends(get_store),
    rem: RemediationStore = Depends(get_rem_store),
) -> dict[str, Any]:
    report = _find_report(store, body.incident_id)
    if not report:
        raise api_error(404, "not_found", f"Incident not found: {body.incident_id}")

    has_manual = bool(body.manual_prompt)
    if not has_manual and not body.regen_of_plan_id:
        from remediation.auto import auto_remediate_from_report

        auto = auto_remediate_from_report(report)
        if auto and auto.get("plan_id"):
            return {"plan": rem.get(auto["plan_id"]), "auto": auto}

    from remediation.service import propose_for_report

    result = propose_for_report(
        report,
        manual_prompt=body.manual_prompt,
        prompt_mode=body.prompt_mode,
        regen_of_plan_id=body.regen_of_plan_id,
        parent_plan_id=body.regen_of_plan_id,
    )
    if not result.get("ok") or not result.get("plan"):
        raise api_error(400, "propose_failed", result.get("error") or "propose failed")
    saved = rem.upsert_proposed(result["plan"], force_new=has_manual or bool(body.regen_of_plan_id))
    return {
        "plan": saved,
        "nim_health": result.get("nim_health"),
        "source": result["plan"].get("source"),
        "previous_plan_id": body.regen_of_plan_id,
    }


@router.post("/plans/{plan_id}/regenerate")
def regenerate(
    plan_id: str,
    body: RegenerateBody | None = None,
    store: DashboardStore = Depends(get_store),
    rem: RemediationStore = Depends(get_rem_store),
) -> dict[str, Any]:
    body = body or RegenerateBody()
    parent = rem.get(plan_id)
    if not parent:
        raise api_error(404, "not_found", "Remediation plan not found")
    incident_id = str(parent.get("incident_id") or "")
    report = _find_report(store, incident_id)
    if not report:
        raise api_error(404, "not_found", f"Incident not found: {incident_id}")

    from remediation.service import propose_for_report

    result = propose_for_report(
        report,
        manual_prompt=body.manual_prompt,
        prompt_mode=body.prompt_mode,
        regen_of_plan_id=plan_id,
        parent_plan_id=plan_id,
        regen_reason=body.reason,
    )
    if not result.get("ok") or not result.get("plan"):
        raise api_error(400, "propose_failed", result.get("error") or "regenerate failed")
    saved = rem.create_proposed(result["plan"])
    rem.add_event(
        plan_id,
        "regenerated",
        {
            "new_plan_id": saved["id"],
            "reason": body.reason,
            "prompt_mode": body.prompt_mode,
            "has_manual_prompt": bool(body.manual_prompt),
        },
    )
    rem.add_event(
        saved["id"],
        "regenerated_from",
        {
            "parent_plan_id": plan_id,
            "reason": body.reason,
            "prompt_mode": body.prompt_mode,
        },
    )
    return {
        "plan": saved,
        "previous_plan": parent,
        "nim_health": result.get("nim_health"),
        "source": result["plan"].get("source"),
    }


@router.post("/plans/{plan_id}/approve")
def approve(
    plan_id: str,
    body: DecisionBody | None = None,
    rem: RemediationStore = Depends(get_rem_store),
) -> dict[str, Any]:
    body = body or DecisionBody()
    row = rem.get(plan_id)
    if not row:
        raise api_error(404, "not_found", "Remediation plan not found")
    if row.get("status") not in APPROVABLE:
        raise api_error(409, "invalid_state", f"Cannot approve plan in status={row.get('status')}")

    from remediation.plan_schema import TypedPlanSpec
    from remediation.policy import check_plan_executable

    raw_spec = row.get("plan_spec") or (row.get("details") or {}).get("plan_spec")
    if isinstance(raw_spec, dict):
        try:
            spec = TypedPlanSpec.from_dict(raw_spec)
            decision = check_plan_executable(spec)
            if not decision.allowed:
                raise api_error(403, decision.code, decision.reason)
        except ValueError as exc:
            raise api_error(400, "invalid_plan_spec", str(exc)) from exc
    elif row.get("depth") == "stub" or row.get("action") in ("alert_only", "scale_restart_cordon"):
        if row.get("action") == "scale_restart_cordon" and os.getenv("CAUSA_REMEDIATION_ALLOW_INFRA") not in (
            "1",
            "true",
            "True",
        ):
            raise api_error(403, "infra_blocked", "infra actions are approval-only / not executable")

    decided_by = body.decided_by or "operator"
    updated = rem.set_status(plan_id, "approved", decided_by=decided_by)
    rem.add_event(plan_id, "approved", {"decided_by": decided_by, "queued_for_worker": True})
    return {
        "plan": updated,
        "message": "Plan approved; remediation worker will claim and execute",
    }


@router.post("/plans/{plan_id}/reject")
def reject(
    plan_id: str,
    body: DecisionBody | None = None,
    rem: RemediationStore = Depends(get_rem_store),
) -> dict[str, Any]:
    body = body or DecisionBody()
    row = rem.get(plan_id)
    if not row:
        raise api_error(404, "not_found", "Remediation plan not found")
    decided_by = body.decided_by or "operator"
    updated = rem.set_status(plan_id, "rejected", decided_by=decided_by)
    rem.add_event(plan_id, "rejected", {"decided_by": decided_by})
    return {"plan": updated}
