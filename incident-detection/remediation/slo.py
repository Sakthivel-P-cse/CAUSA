"""Live Prometheus SLO snapshots for remediation gates."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SLOSnapshot:
    service: str
    error_rate: float | None
    latency_p99_ms: float | None
    success_rate: float | None
    fresh: bool
    queried_at: str
    source: str = "prometheus"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: None if isinstance(value, float) and not math.isfinite(value) else value
                for key, value in asdict(self).items()}


def _prom_url() -> str:
    return os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")


def _instant(query: str) -> list[dict[str, Any]]:
    url = f"{_prom_url()}/api/v1/query"
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url, params={"query": query})
        resp.raise_for_status()
        payload = resp.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"prometheus status={payload.get('status')}")
    return list((payload.get("data") or {}).get("result") or [])


def _scalar(results: list[dict[str, Any]]) -> float | None:
    if not results:
        return None
    try:
        return float(results[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def snapshot_service(service: str, *, window: str = "5m") -> SLOSnapshot:
    now = datetime.now(timezone.utc).isoformat()
    svc = service.replace('"', "")
    try:
        # Zero-error traffic has no error series; retain a zero numerator.
        err_q = (
            f'(sum(rate(traces_span_metrics_calls_total{{service_name="{svc}",'
            f'status_code="STATUS_CODE_ERROR"}}[{window}])) or vector(0)) '
            f'/ clamp_min(sum(rate(traces_span_metrics_calls_total{{service_name="{svc}"}}[{window}])), 1e-9)'
        )
        lat_q = (
            f'histogram_quantile(0.99, sum by (le) (rate('
            f'traces_span_metrics_duration_milliseconds_bucket{{service_name="{svc}"}}[{window}])))'
        )
        # All-error traffic has no success series; retain a zero numerator.
        ok_q = (
            f'(sum(rate(traces_span_metrics_calls_total{{service_name="{svc}",'
            f'status_code!="STATUS_CODE_ERROR"}}[{window}])) or vector(0)) '
            f'/ clamp_min(sum(rate(traces_span_metrics_calls_total{{service_name="{svc}"}}[{window}])), 1e-9)'
        )
        fallback_err = (
            f'(sum(rate(http_server_request_duration_seconds_count{{service="{svc}",'
            f'http_response_status_code=~"5.."}}[{window}])) or vector(0)) '
            f'/ clamp_min(sum(rate(http_server_request_duration_seconds_count{{service="{svc}"}}[{window}])), 1e-9)'
        )
        error_rate = _scalar(_instant(err_q))
        if error_rate is None:
            error_rate = _scalar(_instant(fallback_err))
        latency = _scalar(_instant(lat_q))
        success = _scalar(_instant(ok_q))
        # Match the chart's 30s scrape interval; rate() can outlive its source samples.
        source_ages = []
        for metric in ("traces_span_metrics_calls_total", "traces_span_metrics_duration_milliseconds_bucket"):
            selector = f'{metric}{{service_name="{svc}"}}'
            contributors = f'rate({selector}[{window}])'
            timestamps = f'timestamp({selector}) and {contributors}'
            # A disappeared series can still contribute to rate(); require every contributor.
            query = f'(time() - min({timestamps})) and (count({contributors}) == count({timestamps}))'
            source_ages.append(_scalar(_instant(query)))
        fresh = all(age is not None and math.isfinite(age) and 0 <= age <= 30 for age in source_ages)
        return SLOSnapshot(
            service=service,
            error_rate=error_rate,
            latency_p99_ms=latency,
            success_rate=success,
            fresh=fresh,
            queried_at=now,
            error=None if fresh else "prometheus source samples missing or older than 30s",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("slo snapshot failed for %s: %s", service, exc)
        return SLOSnapshot(
            service=service,
            error_rate=None,
            latency_p99_ms=None,
            success_rate=None,
            fresh=False,
            queried_at=now,
            error=str(exc),
        )


def evaluate_gates(
    snap: SLOSnapshot,
    *,
    error_rate_max: float,
    latency_p99_ms_max: float,
    success_rate_min: float,
    require_fresh: bool = True,
) -> dict[str, Any]:
    values = (snap.error_rate, snap.latency_p99_ms, snap.success_rate)
    if any(value is None or not math.isfinite(value) for value in values) or (require_fresh and not snap.fresh):
        return {"pass": False, "verified": False, "reason": snap.error or "metrics missing, stale or non-finite", "snapshot": snap.to_dict()}
    if snap.error_rate is not None and snap.error_rate > error_rate_max:
        return {
            "pass": False,
            "verified": True,
            "reason": f"error_rate {snap.error_rate:.4f} > {error_rate_max}",
            "snapshot": snap.to_dict(),
        }
    if snap.latency_p99_ms is not None and snap.latency_p99_ms > latency_p99_ms_max:
        return {
            "pass": False,
            "verified": True,
            "reason": f"latency_p99 {snap.latency_p99_ms:.1f}ms > {latency_p99_ms_max}",
            "snapshot": snap.to_dict(),
        }
    if snap.success_rate is not None and snap.success_rate < success_rate_min:
        return {
            "pass": False,
            "verified": True,
            "reason": f"success_rate {snap.success_rate:.4f} < {success_rate_min}",
            "snapshot": snap.to_dict(),
        }
    return {"pass": True, "verified": True, "reason": "gates ok", "snapshot": snap.to_dict()}
