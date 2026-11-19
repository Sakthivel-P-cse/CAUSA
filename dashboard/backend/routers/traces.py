from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from dashboard.backend.dependencies import get_tempo
from dashboard.backend.errors import api_error, raise_upstream_error

router = APIRouter()


@router.get("")
def search_traces(
    service: str | None = Query(None),
    error_only: bool = Query(False),
    minutes: int = Query(30, ge=1, le=1440),
    limit: int = Query(20, ge=1, le=100),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    tempo=Depends(get_tempo),
) -> list[str]:
    if start is not None and end is not None and start > end:
        raise api_error(422, "invalid_time_range", "Start time must not be after end time")
    try:
        if start is not None and end is not None:
            if error_only:
                ids = tempo.search_error_traces(
                    service=service, lookback_minutes=minutes, limit=limit, start=start, end=end
                )
            else:
                ids = tempo.search_traces_in_window(
                    service=service, start=start, end=end, limit=limit
                )
        elif error_only:
            ids = tempo.search_error_traces(
                service=service, lookback_minutes=minutes, limit=limit
            )
        else:
            ids = tempo.search_traces(
                service=service, lookback_minutes=minutes, limit=limit
            )
    except Exception as exc:
        raise_upstream_error("tempo", "search traces", exc)
    return ids


@router.get("/{trace_id}")
def get_trace(trace_id: str, tempo=Depends(get_tempo)) -> dict[str, Any]:
    try:
        spans = tempo.get_trace(trace_id)
    except Exception as exc:
        raise_upstream_error("tempo", "get trace", exc, not_found=True)
    return {
        "trace_id": trace_id,
        "span_count": len(spans),
        "spans": [
            {
                "span_id": s.span_id,
                "parent_span_id": s.parent_span_id,
                "service": s.service,
                "operation": s.operation,
                "duration_ms": s.duration_ms,
                "status_code": s.status_code,
                "error_message": s.error_message,
                "start_time": s.start_time.timestamp() * 1000,
            }
            for s in spans
        ],
    }
