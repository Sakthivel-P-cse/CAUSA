from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from dashboard.backend.dependencies import get_store
from dashboard.backend.db import DashboardStore
from dashboard.backend.errors import api_error
from dashboard.backend.remediation_store import RemediationStore

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


class DecisionBody(BaseModel):
    decided_by: str | None = "operator"
    breach: bool = False


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


@router.post("/propose")
def propose(
    body: ProposeBody,
    store: DashboardStore = Depends(get_store),
    rem: RemediationStore = Depends(get_rem_store),
) -> dict[str, Any]:
    report = _find_report(store, body.incident_id)
    if not report:
        raise api_error(404, "not_found", f"Incident not found: {body.incident_id}")
    from remediation.service import propose_for_report

    result = propose_for_report(report)
    if not result.get("ok") or not result.get("plan"):
        raise api_error(400, "propose_failed", result.get("error") or "propose failed")
    saved = rem.upsert_proposed(result["plan"])
    return {"plan": saved, "nim_health": result.get("nim_health"), "source": result["plan"].get("source")}


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
    if row.get("status") not in ("proposed", "failed", "rolled_back"):
        raise api_error(409, "invalid_state", f"Cannot approve plan in status={row.get('status')}")
    rem.set_status(plan_id, "applying", decided_by=body.decided_by)
    from remediation.service import apply_plan_v1

    apply_result = apply_plan_v1(row, breach=body.breach)
    status = str(apply_result.get("status") or "failed")
    updated = rem.set_status(
        plan_id,
        status if status in ("succeeded", "failed", "rolled_back") else "failed",
        decided_by=body.decided_by,
        apply_result=apply_result,
    )
    rem.add_event(plan_id, "approved", {"apply": apply_result})
    return {"plan": updated, "apply_result": apply_result}


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
    updated = rem.set_status(plan_id, "rejected", decided_by=body.decided_by)
    rem.add_event(plan_id, "rejected", {})
    return {"plan": updated}
