"""
Causa Dev-A ingestion package (`ingest`) — the C1 telemetry read layer.

Reads the Stage-1 OTel stack (Tempo/Prometheus/Loki) and normalizes it into the
shared `contracts` types. Two surfaces:

  • TelemetryAdapter — the C1 adapters (traces / metrics / logs / topology).
  • TelemetryReader  — the one-shot `read_incident_window()` bundle over them.

    from ingest import TelemetryAdapter, TelemetryReader
    from contracts import Window
    from datetime import datetime, timezone, timedelta

    adapter = TelemetryAdapter.from_defaults(namespace="otel-demo")
    now = datetime.now(timezone.utc)
    edges = adapter.topology(Window(now - timedelta(minutes=10), now))

The normalized TYPES live in `contracts` (the cross-dev seam) — import them from
there, not from here. This package imports nothing from RCA/remediation.
"""

from __future__ import annotations

from .adapters import TelemetryAdapter
from .clients.loki_client import LokiClient
from .clients.prometheus_client import PrometheusClient
from .clients.tempo_client import TempoClient
from .reader import TelemetryReader

__all__ = [
    "TelemetryAdapter",
    "TelemetryReader",
    "PrometheusClient",
    "TempoClient",
    "LokiClient",
]
