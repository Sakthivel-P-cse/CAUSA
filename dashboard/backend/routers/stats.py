from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query

from dashboard.backend.ack_store import get_acks, lifecycle_for, merge_ack_fields
from dashboard.backend.dependencies import get_store
from dashboard.backend.db import DashboardStore
from dashboard.backend.remediation_store import RemediationStore

router = APIRouter()


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _mean_seconds(pairs: list[float]) -> float | None:
    if not pairs:
        return None
    return sum(pairs) / len(pairs)


@router.get("")
def get_stats(
    service: str | None = Query(None),
    minutes: int = Query(360, ge=1, le=1440),
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    store: DashboardStore = Depends(get_store),
) -> dict[str, Any]:
    acks = get_acks()
    items = [merge_ack_fields(i, acks) for i in store.list_incidents_joined() if i.get("has_rca")]
    end_dt = end or datetime.now(timezone.utc)
    start_dt = start or (end_dt - timedelta(minutes=minutes))
    if start_dt > end_dt:
        from dashboard.backend.errors import api_error
        raise api_error(422, "invalid_time_range", "Start time must not be after end time")
    if service:
        items = [
            item for item in items
            if service in {
                item.get("root_cause_service"),
                item.get("alerted_service"),
            }
        ]
    items = [
        item for item in items
        if (timestamp := _parse_ts(item.get("triggered_at")))
        and start_dt <= timestamp <= end_dt
    ]
    by_failure = Counter(str(i.get("failure_type") or "Unclassified") for i in items)
    by_service = Counter(
        str(i.get("root_cause_service") or i.get("alerted_service") or "unknown")
        for i in items
    )
    confs = [float(i["confidence_score"]) for i in items if i.get("confidence_score") is not None]
    per_day: Counter[str] = Counter()
    mtta: list[float] = []
    mttr: list[float] = []
    complete = degraded = inconclusive = 0
    feedback_pairs: list[dict[str, Any]] = []

    for i in items:
        ts = i.get("triggered_at")
        if ts:
            try:
                day = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date().isoformat()
                per_day[day] += 1
            except Exception:
                pass

        triggered = _parse_ts(i.get("triggered_at"))
        acked = _parse_ts(i.get("acked_at"))
        resolved = _parse_ts(i.get("resolved_at"))
        if triggered and acked and acked >= triggered:
            mtta.append((acked - triggered).total_seconds())
        if triggered and resolved and resolved >= triggered:
            mttr.append((resolved - triggered).total_seconds())

        summary = str(i.get("root_cause_summary") or "").lower()
        conf = i.get("confidence_score")
        if summary.startswith("rca abstained") or conf is None:
            inconclusive += 1
        elif conf is not None and float(conf) < 0.25:
            degraded += 1
        else:
            complete += 1

        fb = i.get("feedback") or {}
        verdict = str(fb.get("verdict") or "").lower()
        if verdict in {"accepted", "rejected"} and conf is not None:
            feedback_pairs.append(
                {
                    "predicted": float(conf),
                    "correct": 1.0 if verdict == "accepted" else 0.0,
                }
            )

    rem = RemediationStore().stats()
    counts = rem.get("by_status", {})
    decided = sum(int(counts.get(status, 0)) for status in ("succeeded", "failed", "rolled_back"))
    rate = (int(counts.get("succeeded", 0)) / decided) if decided else None

    lifecycle = Counter(lifecycle_for(i) for i in items)

    return {
        "total": len(items),
        "by_failure_type": dict(by_failure),
        "top_services": [
            {"service": s, "count": c} for s, c in by_service.most_common(10)
        ],
        "incidents_per_day": sorted(per_day.items()),
        "avg_confidence": (sum(confs) / len(confs)) if confs else 0.0,
        "confidence_calibrated": False,
        "confidence_sample_size": len(confs),
        "feedback_sample_size": len(feedback_pairs),
        "failure_type_count": len(by_failure),
        "mtta_seconds": _mean_seconds(mtta),
        "mttr_seconds": _mean_seconds(mttr),
        "mtta_n": len(mtta),
        "mttr_n": len(mttr),
        "auto_remediation_rate": None,
        "remediation_success_rate": rate,
        "analyses_complete": complete,
        "analyses_degraded": degraded,
        "analyses_inconclusive": inconclusive,
        "lifecycle": dict(lifecycle),
        "active_incidents": lifecycle.get("active", 0) + lifecycle.get("investigating", 0),
        "feedback_calibration": feedback_pairs,
        "remediation": rem,
    }
