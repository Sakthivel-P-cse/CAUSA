"""Live Prometheus SLO snapshots for remediation gates."""

from __future__ import annotations

import logging
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
        return asdict(self)


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
        # `or vector(0)` on the numerator: a service with traffic but ZERO errors
        # matches no series, so bare sum() yields an empty vector and the whole
        # division collapses to empty — indistinguishable from "no data". That
        # made error_rate None instead of 0.0, and evaluate_gates skips a None
        # gate, so the error-rate check silently never ran on a healthy service.
        err_q = (
            f'(sum(rate(traces_span_metrics_calls_total{{service_name="{svc}",'
            f'status_code="STATUS_CODE_ERROR"}}[{window}])) or vector(0)) '
            f'/ clamp_min(sum(rate(traces_span_metrics_calls_total{{service_name="{svc}"}}[{window}])), 1e-9)'
        )
        lat_q = (
            f'histogram_quantile(0.99, sum by (le) (rate('
            f'traces_span_metrics_duration_milliseconds_bucket{{service_name="{svc}"}}[{window}])))'
        )
        # Same guard, and here it matters more: a service that is failing EVERY
        # request matches no non-error series, so without `or vector(0)` the
        # success rate reads None and its gate is skipped at exactly the moment
        # it should fail closed.
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
        fresh = error_rate is not None or latency is not None or success is not None
        if not fresh:
            return SLOSnapshot(
                service=service,
                error_rate=None,
                latency_p99_ms=None,
                success_rate=None,
                fresh=False,
                queried_at=now,
                error="no prometheus samples for service",
            )
        return SLOSnapshot(
            service=service,
            error_rate=error_rate,
            latency_p99_ms=latency,
            success_rate=success,
            fresh=True,
            queried_at=now,
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
    if require_fresh and not snap.fresh:
        return {"pass": False, "reason": snap.error or "metrics missing or stale", "snapshot": snap.to_dict()}
    if snap.error_rate is not None and snap.error_rate > error_rate_max:
        return {
            "pass": False,
            "reason": f"error_rate {snap.error_rate:.4f} > {error_rate_max}",
            "snapshot": snap.to_dict(),
        }
    if snap.latency_p99_ms is not None and snap.latency_p99_ms > latency_p99_ms_max:
        return {
            "pass": False,
            "reason": f"latency_p99 {snap.latency_p99_ms:.1f}ms > {latency_p99_ms_max}",
            "snapshot": snap.to_dict(),
        }
    if snap.success_rate is not None and snap.success_rate < success_rate_min:
        return {
            "pass": False,
            "reason": f"success_rate {snap.success_rate:.4f} < {success_rate_min}",
            "snapshot": snap.to_dict(),
        }
    return {"pass": True, "reason": "gates ok", "snapshot": snap.to_dict()}
