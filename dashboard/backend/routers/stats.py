from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends

from dashboard.backend.dependencies import get_store
from dashboard.backend.db import DashboardStore

router = APIRouter()


@router.get("")
def get_stats(store: DashboardStore = Depends(get_store)) -> dict[str, Any]:
    items = [i for i in store.list_incidents_joined() if i.get("has_rca")]
    by_failure = Counter(str(i.get("failure_type") or "Unclassified") for i in items)
    by_service = Counter(
        str(i.get("root_cause_service") or i.get("alerted_service") or "unknown")
        for i in items
    )
    confs = [float(i["confidence_score"]) for i in items if i.get("confidence_score") is not None]
    per_day: Counter[str] = Counter()
    for i in items:
        ts = i.get("triggered_at")
        if not ts:
            continue
        try:
            day = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date().isoformat()
            per_day[day] += 1
        except Exception:
            continue
    return {
        "total": len(items),
        "by_failure_type": dict(by_failure),
        "top_services": [
            {"service": s, "count": c} for s, c in by_service.most_common(10)
        ],
        "incidents_per_day": sorted(per_day.items()),
        "avg_confidence": (sum(confs) / len(confs)) if confs else 0.0,
        "failure_type_count": len(by_failure),
    }
