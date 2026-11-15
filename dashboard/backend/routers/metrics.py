from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from dashboard.backend.dependencies import get_prometheus, window_bounds
from dashboard.backend.errors import api_error, raise_upstream_error

router = APIRouter()


def _scoped_service_name(raw: str | None) -> str | None:
    from ingest.service_scope import is_monitored_service, normalize_service_name

    name = normalize_service_name(raw) or (raw.strip() if raw else None)
    if not name or not is_monitored_service(name):
        return None
    return name


@router.get("/services")
def list_services(prom=Depends(get_prometheus)) -> list[dict[str, Any]]:
    try:
        prom.check_available()
        edges = prom.get_service_edges(window="5m")
    except Exception as exc:
        raise_upstream_error("prometheus", "list services", exc)

    by_service: dict[str, dict[str, float]] = {}
    for e in edges:
        raw = getattr(e, "callee", None) or getattr(e, "server", None)
        svc = _scoped_service_name(raw)
        if not svc:
            continue
        if svc not in by_service:
            by_service[svc] = {"error_rate": 0.0, "latency_p99_ms": 0.0, "request_rate": 0.0}
        by_service[svc]["error_rate"] = max(
            by_service[svc]["error_rate"], float(getattr(e, "error_rate", 0) or 0)
        )
        by_service[svc]["latency_p99_ms"] = max(
            by_service[svc]["latency_p99_ms"], float(getattr(e, "latency_p99_ms", 0) or 0)
        )
        by_service[svc]["request_rate"] += float(getattr(e, "request_rate", 0) or 0)

    result = []
    for svc, metrics in by_service.items():
        if metrics["error_rate"] > 0.05:
            health = "red"
        elif metrics["latency_p99_ms"] > 1000:
            health = "yellow"
        else:
            health = "green"
        result.append({"name": svc, "health": health, **metrics})
    return sorted(result, key=lambda x: x["name"])


@router.get("/service/{name}/series")
def service_series(
    name: str,
    metric: str = Query("error_rate", enum=["error_rate", "latency_p99_ms", "request_rate"]),
    minutes: int = Query(60, ge=5, le=1440),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    prom=Depends(get_prometheus),
) -> dict[str, Any]:
    if start is not None and end is not None and start > end:
        raise api_error(422, "invalid_time_range", "Start time must not be after end time")
    start_dt, end_dt = window_bounds(minutes, start, end)
    try:
        if metric == "error_rate":
            series = prom.get_error_rate_series(name, start_dt, end_dt)
        elif metric == "latency_p99_ms":
            series = prom.get_latency_p99_series(name, start_dt, end_dt)
        else:
            series = prom.get_request_rate_series(name, start_dt, end_dt)
    except Exception as exc:
        raise_upstream_error("prometheus", "get service series", exc)
    return {"service": name, "metric": metric, "series": series}
