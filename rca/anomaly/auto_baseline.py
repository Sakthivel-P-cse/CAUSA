from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from contracts import Baseline, Window

from .baseline import robust_baseline

logger = logging.getLogger(__name__)

_SERVICE_METRICS = ("error_rate", "p95_latency", "request_rate")
_DEFAULT_MINUTES = int(os.getenv("CAUSA_AUTO_BASELINE_MINUTES", "15"))
_inflight: set[str] = set()
_inflight_lock = threading.Lock()


def discover_service_names(prom, *, window_minutes: int) -> set[str]:
    services: set[str] = set()
    try:
        services |= prom.get_active_service_names()
    except Exception:
        pass
    try:
        for edge in prom.get_service_edges(window=f"{window_minutes}m"):
            if edge.caller:
                services.add(edge.caller)
            if edge.callee:
                services.add(edge.callee)
    except Exception:
        pass
    return {s for s in services if s and s.lower() not in {"unknown", "none"}}


def capture_service_baselines(
    prom,
    service: str,
    *,
    start: datetime,
    end: datetime,
    step: str = "1m",
) -> dict[tuple[str, str], Baseline]:
    baselines: dict[tuple[str, str], Baseline] = {}
    series_map = {
        "error_rate": prom.get_error_rate_series(service, start, end, step),
        "p95_latency": prom.get_latency_p95_series(service, start, end, step),
        "request_rate": prom.get_request_rate_series(service, start, end, step),
    }
    for metric, series in series_map.items():
        if not series:
            continue
        baseline = robust_baseline(point[1] for point in series)
        if baseline is not None:
            baselines[(service, metric)] = baseline
    return baselines


def capture_live_baselines(
    prom,
    store,
    *,
    window_minutes: int | None = None,
    extra_services: set[str] | None = None,
    service_filter=None,
) -> dict[tuple[str, str], Baseline]:
    minutes = window_minutes if window_minutes is not None else int(
        os.getenv("BASELINE_WINDOW_MINUTES", str(_DEFAULT_MINUTES))
    )
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    services = discover_service_names(prom, window_minutes=minutes)
    if extra_services:
        services |= extra_services
    if service_filter is not None:
        services = {s for s in services if service_filter(s)}
    baselines: dict[tuple[str, str], Baseline] = {}
    for service in sorted(services):
        baselines.update(
            capture_service_baselines(prom, service, start=start, end=end)
        )
    if baselines and store is not None:
        store.upsert_baselines(baselines)
    return baselines


def _capture_one_service_async(service: str, prom_factory, store_factory, minutes: int) -> None:
    with _inflight_lock:
        if service in _inflight:
            return
        _inflight.add(service)
    try:
        prom = prom_factory()
        store = store_factory()
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=minutes)
            baselines = capture_service_baselines(prom, service, start=start, end=end)
            if baselines:
                store.upsert_baselines(baselines)
                logger.info(
                    "Auto-baseline captured %d metrics for new service %s (%dm)",
                    len(baselines),
                    service,
                    minutes,
                )
        finally:
            prom.close()
    except Exception:
        logger.exception("Auto-baseline failed for service %s", service)
    finally:
        with _inflight_lock:
            _inflight.discard(service)


def register_auto_baseline_hook(
    registry,
    *,
    prom_factory,
    store_factory,
    window_minutes: int | None = None,
) -> None:
    minutes = window_minutes if window_minutes is not None else _DEFAULT_MINUTES

    def on_new(service: str) -> None:
        threading.Thread(
            target=_capture_one_service_async,
            args=(service, prom_factory, store_factory, minutes),
            name=f"auto-baseline-{service}",
            daemon=True,
        ).start()

    registry.add_listener(on_new)


def warmup_from_adapter(adapter, services, window: Window, namespace: str | None = None):
    from .baseline import capture_warmup

    return capture_warmup(adapter, services, window, namespace=namespace)


def main() -> int:
    import sys

    from ingest.clients.prometheus_client import PrometheusClient
    from ingest.service_scope import is_monitored_service
    from ingest.trigger.state import IncidentStore, dsn_from_env

    prom_url = os.getenv("PROMETHEUS_URL", "http://prometheus.prometheus.svc.cluster.local:9090")
    minutes = int(os.getenv("BASELINE_WINDOW_MINUTES", str(_DEFAULT_MINUTES)))
    prom = PrometheusClient(prom_url)
    store = IncidentStore(dsn_from_env())
    try:
        baselines = capture_live_baselines(
            prom,
            store,
            window_minutes=minutes,
            service_filter=is_monitored_service,
        )
        if not baselines:
            print("No baselines captured (no traffic or metrics yet)", file=sys.stderr)
            return 2
        print(f"Captured {len(baselines)} baseline rows")
        return 0
    finally:
        prom.close()


if __name__ == "__main__":
    raise SystemExit(main())
