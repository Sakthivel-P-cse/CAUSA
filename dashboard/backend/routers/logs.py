from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from dashboard.backend.dependencies import get_loki
from dashboard.backend.errors import api_error, raise_upstream_error

router = APIRouter()


@router.get("")
def get_logs(
    service: str | None = Query(None),
    level: str | None = Query(None, enum=["info", "warn", "error", "debug"]),
    search: str | None = Query(None, max_length=200),
    minutes: int = Query(30, ge=1, le=1440),
    limit: int = Query(200, ge=1, le=1000),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    loki=Depends(get_loki),
) -> list[dict[str, Any]]:
    if start is not None and end is not None and start > end:
        raise api_error(422, "invalid_time_range", "Start time must not be after end time")
    try:
        entries = loki.get_logs(
            service=service,
            level=level,
            search=search,
            lookback_minutes=minutes,
            limit=limit,
            start=start,
            end=end,
        )
    except Exception as exc:
        raise_upstream_error("loki", "get logs", exc)

    from ingest.service_scope import is_monitored_service, normalize_service_name

    scoped: list[dict[str, Any]] = []
    for entry in entries:
        raw = entry.get("service")
        name = normalize_service_name(raw) or raw
        if not is_monitored_service(name):
            continue
        entry = dict(entry)
        entry["service"] = name
        scoped.append(entry)
    return scoped
