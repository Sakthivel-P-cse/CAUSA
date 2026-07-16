"""
TraceOps Stage-1 Ingestion Module — public API.

A loosely-coupled, drop-in ingestion module. It reads from the Stage-1
OTel stack (Tempo/Prometheus/Loki) and normalizes one incident window into
a single typed `IncidentTelemetry` object. Consumers depend ONLY on the
types exported here — never on raw backend responses, and never the reverse
(ingestion imports nothing from RCA/remediation/dashboard).

Typical use by any downstream consumer:

    from ingestion import TelemetryReader
    from datetime import datetime, timezone

    reader = TelemetryReader.from_defaults()
    incident = reader.read_incident_window(
        incident_id="inc-123",
        alerted_service="payment-service",
        around=datetime.now(timezone.utc),
        lookback_minutes=10,
        namespace="apps",
    )
    for span_env in incident.spans:
        ...  # span_env.span is a TraceSpan, with dag_depth / logical_seq
    err = incident.metric_series("payment-service", "error_rate")
"""

from __future__ import annotations

from .contract import (
    IncidentTelemetry,
    InfraNode,
    LogRecordEnvelope,
    MetricPointEnvelope,
    OrderingSource,
    ServiceEdge,
    SpanEnvelope,
    TelemetryReference,
    TraceSpan,
)
from .reader import TelemetryReader
from .clients.loki_client import LokiClient
from .clients.prometheus_client import PrometheusClient
from .clients.tempo_client import TempoClient

__all__ = [
    # Entry point
    "TelemetryReader",
    # The normalized handoff object
    "IncidentTelemetry",
    # Signal envelopes
    "SpanEnvelope",
    "MetricPointEnvelope",
    "LogRecordEnvelope",
    # Graph primitives
    "TraceSpan",
    "ServiceEdge",
    "InfraNode",
    # Provenance
    "TelemetryReference",
    "OrderingSource",
    # Backend clients (for advanced/custom wiring)
    "PrometheusClient",
    "TempoClient",
    "LokiClient",
]
