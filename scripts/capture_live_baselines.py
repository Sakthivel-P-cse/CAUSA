#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "incidents"),
    str(ROOT / ".import"),
    str(ROOT),
    str(ROOT / "shared"),
    str(ROOT / "rca"),
]

from ingest.clients.prometheus_client import PrometheusClient  # noqa: E402
from ingest.service_scope import is_monitored_service  # noqa: E402
from ingest.trigger.state import IncidentStore, dsn_from_env  # noqa: E402
from anomaly.auto_baseline import capture_live_baselines  # noqa: E402


def main() -> int:
    minutes = int(os.getenv("BASELINE_WINDOW_MINUTES", "30"))
    prom_url = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090")
    prom = PrometheusClient(prom_url)
    store = IncidentStore(dsn_from_env())
    try:
        extra_raw = os.getenv("BASELINE_SERVICES", "").strip()
        extra = {s.strip() for s in extra_raw.split(",") if s.strip()} if extra_raw else None
        baselines = capture_live_baselines(
            prom,
            store,
            window_minutes=minutes,
            extra_services=extra,
            service_filter=is_monitored_service,
        )
        if not baselines:
            print(
                "WARN: no metric series yet — baselines deferred until traffic exists",
                file=sys.stderr,
            )
            return 2
        services = len({svc for svc, _ in baselines})
        print(f"Wrote {len(baselines)} baselines for {services} services over {minutes}m")
        return 0
    finally:
        prom.close()


if __name__ == "__main__":
    raise SystemExit(main())
