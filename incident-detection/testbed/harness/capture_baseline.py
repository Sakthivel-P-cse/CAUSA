"""
capture_baseline.py — learn robust warmup baselines from a HEALTHY window.

Run this BEFORE injecting any chaos (Dev_A brief M1): it captures median/MAD per
(service, metric) over a healthy window and writes them to JSON for the anomaly
module + eval harness to load. The scorer never sees injected truth — only these
healthy-window statistics.

Usage:
    python -m testbed.harness.capture_baseline \
        --namespace otel-demo --window-minutes 30 --out baselines/otel-demo.json
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from anomaly import capture_warmup, save_baselines
from contracts import CallType, Window
from ingest import TelemetryAdapter

logger = logging.getLogger("causa.baseline")


def discover_services(adapter: TelemetryAdapter, window: Window) -> list[str]:
    """Service names from the topology (both ends of non-runs-on edges)."""
    services: set[str] = set()
    for e in adapter.topology(window):
        if e.call_type is CallType.RUNS_ON:
            services.add(e.dst)  # the hosted service
        else:
            services.add(e.src)
            services.add(e.dst)
    return sorted(s for s in services if s and s != "unknown")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Capture robust warmup baselines")
    p.add_argument("--namespace", default="otel-demo")
    p.add_argument("--window-minutes", type=int, default=30)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--services", nargs="*", help="explicit service list (else auto-discover)")
    p.add_argument("--prometheus-url", default=None)
    p.add_argument("--tempo-url", default=None)
    p.add_argument("--loki-url", default=None)
    args = p.parse_args(argv)

    kwargs = {}
    if args.prometheus_url:
        kwargs["prometheus_url"] = args.prometheus_url
    if args.tempo_url:
        kwargs["tempo_url"] = args.tempo_url
    if args.loki_url:
        kwargs["loki_url"] = args.loki_url

    adapter = TelemetryAdapter.from_defaults(namespace=args.namespace, **kwargs)
    try:
        end = datetime.now(timezone.utc)
        window = Window(end - timedelta(minutes=args.window_minutes), end)
        services = args.services or discover_services(adapter, window)
        logger.info("Capturing baselines for %d services over a %dm healthy window",
                    len(services), args.window_minutes)
        baselines = capture_warmup(adapter, services, window, namespace=args.namespace)
    finally:
        adapter.close()

    save_baselines(baselines, args.out)
    logger.info("Wrote %d baselines → %s", len(baselines), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
