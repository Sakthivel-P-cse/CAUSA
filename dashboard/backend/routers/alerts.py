from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from dashboard.backend.app_scope import is_app_scope_incident
from dashboard.backend.dependencies import get_store
from dashboard.backend.db import DashboardStore

router = APIRouter()

_INFRA_ALERT_HINTS = (
    "pod",
    "node",
    "deployment",
    "replica",
    "memory",
    "cpu",
    "oom",
    "crash",
    "disk",
    "container",
)


def _category(alert_name: str | None, failure_type: str | None) -> str:
    ft = str(failure_type or "").strip().lower()
    if ft in {"infra", "resource"}:
        return "infra"
    name = str(alert_name or "").lower()
    if any(h in name for h in _INFRA_ALERT_HINTS):
        return "infra"
    return "app"


def _rca_badge(item: dict[str, Any]) -> str:
    summary = str(item.get("root_cause_summary") or "").lower()
    if summary.startswith("rca abstained"):
        return "abstained"
    if item.get("root_cause_service") or item.get("has_rca"):
        return "has_rca"
    return "pending"


@router.get("")
def list_alerts(
    limit: int = Query(20, ge=1, le=100),
    store: DashboardStore = Depends(get_store),
) -> list[dict[str, Any]]:
    joined = store.list_incidents_joined()
    open_items = [
        i
        for i in joined
        if str(i.get("incident_status") or "") != "resolved" and is_app_scope_incident(i)
    ][:limit]
    return [
        {
            "fingerprint": i.get("id") or i.get("incident_id"),
            "incident_id": i.get("incident_id") or i.get("id"),
            "state": "ongoing",
            "status": "ongoing",
            "alert_name": i.get("alert_name"),
            "severity": i.get("severity") or "info",
            "service": i.get("alerted_service"),
            "failure_type": i.get("failure_type"),
            "category": _category(i.get("alert_name"), i.get("failure_type")),
            "rca_status": _rca_badge(i),
            "summary": i.get("root_cause_summary")
            or f"Incident {i.get('incident_id') or i.get('id')}",
            "starts_at": i.get("triggered_at"),
            "root_cause_service": i.get("root_cause_service"),
        }
        for i in open_items
    ]
