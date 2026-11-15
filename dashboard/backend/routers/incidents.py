from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from dashboard.backend.ack_store import (
    ack_incident,
    get_acks,
    lifecycle_for,
    merge_ack_fields,
    snooze_incident,
)
from dashboard.backend.app_scope import is_app_scope_incident
from dashboard.backend.dependencies import get_store
from dashboard.backend.db import DashboardStore
from dashboard.backend.errors import api_error, raise_database_error

router = APIRouter()


def _matches(
    item: dict[str, Any],
    *,
    q: str | None,
    service: str | None,
    failure_type: str | None,
    severity: str | None,
    status: str | None,
    min_confidence: float | None,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if q:
        blob = " ".join(
            str(item.get(k) or "")
            for k in (
                "alert_name",
                "alerted_service",
                "root_cause_service",
                "root_cause_summary",
                "failure_type",
                "incident_id",
            )
        ).lower()
        if q.lower() not in blob:
            return False
    if service:
        svc = service.lower()
        if svc not in str(item.get("alerted_service") or "").lower() and svc not in str(
            item.get("root_cause_service") or ""
        ).lower():
            return False
    if failure_type and str(item.get("failure_type") or "") != failure_type:
        return False
    if severity and str(item.get("severity") or "").lower() != severity.lower():
        return False
    if status and status != "all" and str(item.get("incident_status") or "") != status:
        return False
    if min_confidence is not None:
        conf = item.get("confidence_score")
        if conf is None or float(conf) < min_confidence:
            return False
    triggered = item.get("triggered_at")
    if triggered and start:
        try:
            ts = datetime.fromisoformat(str(triggered).replace("Z", "+00:00"))
            if ts < start:
                return False
        except Exception:
            pass
    if triggered and end:
        try:
            ts = datetime.fromisoformat(str(triggered).replace("Z", "+00:00"))
            if ts > end:
                return False
        except Exception:
            pass
    return True


def _facets(items: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {
        "severity": dict(Counter(str(i.get("severity") or "info") for i in items)),
        "failure_type": dict(
            Counter(str(i.get("failure_type") or "Unclassified") for i in items)
        ),
        "service": dict(
            Counter(
                str(i.get("root_cause_service") or i.get("alerted_service") or "unknown")
                for i in items
            )
        ),
        "status": dict(Counter(str(i.get("incident_status") or "open") for i in items)),
    }


def _list_item(item: dict[str, Any], acks: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = merge_ack_fields(item, acks)
    return {
        "id": merged.get("id"),
        "incident_id": merged.get("incident_id"),
        "triggered_at": merged.get("triggered_at"),
        "alert_name": merged.get("alert_name"),
        "alerted_service": merged.get("alerted_service"),
        "root_cause_service": merged.get("root_cause_service"),
        "failure_type": merged.get("failure_type"),
        "severity": merged.get("severity"),
        "incident_status": merged.get("incident_status"),
        "confidence_score": merged.get("confidence_score"),
        "root_cause_summary": merged.get("root_cause_summary"),
        "trace_id": merged.get("trace_id"),
        "resolved_at": merged.get("resolved_at"),
        "has_rca": merged.get("has_rca", False),
        "acked_at": merged.get("acked_at"),
        "acked_by": merged.get("acked_by"),
        "snoozed_until": merged.get("snoozed_until"),
        "lifecycle": lifecycle_for(merged),
        "routing": merged.get("routing") or merged.get("route"),
        "route": merged.get("route") or merged.get("routing"),
        "runbook_url": merged.get("runbook_url"),
        "layer": merged.get("layer"),
    }


@router.get("")
def list_incidents(
    q: str | None = Query(None),
    service: str | None = Query(None),
    failure_type: str | None = Query(None),
    severity: str | None = Query(None),
    status: str | None = Query("all"),
    min_confidence: float | None = Query(None, ge=0, le=1),
    sort: str = Query("newest"),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    store: DashboardStore = Depends(get_store),
) -> dict[str, Any]:
    items = store.list_incidents_joined()
    filtered = [
        i
        for i in items
        if is_app_scope_incident(i)
        and _matches(
            i,
            q=q,
            service=service,
            failure_type=failure_type,
            severity=severity,
            status=status,
            min_confidence=min_confidence,
            start=start,
            end=end,
        )
    ]
    facets = _facets(filtered)
    if sort == "confidence_desc":
        filtered.sort(key=lambda x: float(x.get("confidence_score") or 0), reverse=True)
    elif sort == "severity_desc":
        rank = {"critical": 4, "high": 3, "warning": 2, "medium": 2, "low": 1, "info": 0}
        filtered.sort(
            key=lambda x: (
                rank.get(str(x.get("severity") or "").lower(), 0),
                float(x.get("confidence_score") or 0),
            ),
            reverse=True,
        )
    else:
        filtered.sort(key=lambda x: x.get("triggered_at") or "", reverse=True)
    page = filtered[offset : offset + limit]
    acks = get_acks()
    return {"items": [_list_item(i, acks) for i in page], "total": len(filtered), "facets": facets}


@router.get("/history")
def list_history(
    q: str | None = Query(None),
    service: str | None = Query(None),
    failure_type: str | None = Query(None),
    status: str = Query("all"),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    store: DashboardStore = Depends(get_store),
) -> dict[str, Any]:
    items = store.list_incidents_joined()
    filtered = [
        i
        for i in items
        if is_app_scope_incident(i)
        and _matches(
            i,
            q=q,
            service=service,
            failure_type=failure_type,
            severity=None,
            status=status,
            min_confidence=None,
            start=None,
            end=None,
        )
    ]
    page = filtered[offset : offset + limit]
    acks = get_acks()
    return {
        "items": [
            {
                **_list_item(i, acks),
                "window_start": i.get("window_start"),
                "window_end": i.get("window_end"),
            }
            for i in page
        ],
        "total": len(filtered),
    }


@router.get("/history/{incident_id}")
def history_detail(
    incident_id: str,
    store: DashboardStore = Depends(get_store),
) -> dict[str, Any]:
    item = store.get_history_item(incident_id)
    if not item:
        raise api_error(404, "not_found", "Incident not found")
    return {
        **item,
        "reports": [
            {
                "id": item.get("id"),
                "triggered_at": item.get("triggered_at"),
                "root_cause_service": item.get("root_cause_service"),
                "failure_type": item.get("failure_type"),
                "confidence_score": item.get("confidence_score"),
            }
        ]
        if item.get("has_rca")
        else [],
    }


@router.get("/stream")
async def stream_incidents(request: Request, store: DashboardStore = Depends(get_store)):
    async def event_gen():
        last = ""
        while True:
            if await request.is_disconnected():
                break
            acks = get_acks()
            items = [_list_item(i, acks) for i in store.list_incidents_joined()[:50]]
            active = [i for i in items if i.get("lifecycle") in {"active", "investigating"}]
            payload = json.dumps({"active": len(active), "items": active[:20]})
            if payload != last:
                last = payload
                yield f"event: incidents\ndata: {payload}\n\n"
            else:
                yield "event: ping\ndata: {}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.get("/{report_id}")
def get_incident(
    report_id: str,
    store: DashboardStore = Depends(get_store),
) -> dict[str, Any]:
    item = store.get_report(report_id)
    if not item:
        raise api_error(404, "not_found", "Report not found")
    anomaly = item.get("anomaly_scores") or {}
    anomaly_list = (
        [{"node": k, "score": v} for k, v in anomaly.items()]
        if isinstance(anomaly, dict)
        else []
    )
    rem = store.load_remediation_index().get(str(item.get("incident_id") or ""))
    merged = merge_ack_fields(item)
    return {
        **merged,
        "lifecycle": lifecycle_for(merged),
        "anomaly_score_list": anomaly_list,
        "remediation": rem,
        "references": {
            "metrics_api": "/api/metrics/services",
            "logs_api": "/api/logs",
            "trace_apis": [f"/api/traces/{item['trace_id']}"] if item.get("trace_id") else [],
        },
    }


@router.post("/{report_id}/ack")
def post_ack(
    report_id: str,
    body: dict[str, Any] | None = None,
    store: DashboardStore = Depends(get_store),
) -> dict[str, Any]:
    item = store.get_report(report_id)
    if not item:
        raise api_error(404, "not_found", "Report not found")
    key = str(item.get("incident_id") or item.get("id") or report_id)
    by = str((body or {}).get("acked_by") or "operator")
    try:
        row = ack_incident(key, by=by)
    except Exception as exc:
        raise_database_error("acknowledge incident", exc)
    return {"status": "ok", "incident_id": key, "lifecycle": "investigating", **row}


@router.post("/{report_id}/snooze")
def post_snooze(
    report_id: str,
    body: dict[str, Any] | None = None,
    store: DashboardStore = Depends(get_store),
) -> dict[str, Any]:
    item = store.get_report(report_id)
    if not item:
        raise api_error(404, "not_found", "Report not found")
    key = str(item.get("incident_id") or item.get("id") or report_id)
    minutes = int((body or {}).get("minutes") or 60)
    by = str((body or {}).get("acked_by") or "operator")
    try:
        row = snooze_incident(key, minutes=minutes, by=by)
    except Exception as exc:
        raise_database_error("snooze incident", exc)
    return {"status": "ok", "incident_id": key, "lifecycle": "investigating", **row}


@router.post("/{report_id}/feedback")
def post_feedback(
    report_id: str,
    body: dict[str, Any],
    store: DashboardStore = Depends(get_store),
) -> dict[str, Any]:
    verdict = str(body.get("verdict") or "").lower()
    if verdict not in {"accepted", "rejected"}:
        raise api_error(422, "invalid_verdict", "verdict must be accepted or rejected")
    notes = body.get("notes")
    try:
        updated = store.update_feedback(report_id, verdict=verdict, notes=notes)
    except Exception as exc:
        raise_database_error("write feedback", exc)
    if updated is None:
        raise api_error(404, "not_found", "Report not found")
    return {
        "status": "ok",
        "report_id": report_id,
        "verdict": verdict,
        "notes": notes,
        "feedback": updated.get("feedback"),
    }
