"""
contracts/ — the Causa cross-dev seam (C1 telemetry, C2 anomaly, C5 ground truth).

This package depends on the stdlib ONLY. Dev A produces these types; Dev B and
Dev C consume them. Changing anything here requires all three devs' approval
(Team_Split §3). Import the types from here — never from a client or a reader.
"""

from __future__ import annotations

# C1 — telemetry
from .telemetry import (
    CallType,
    Edge,
    IncidentTelemetry,
    InfraNode,
    LogRecordEnvelope,
    MetricPointEnvelope,
    OrderingSource,
    ServiceEdge,
    SpanEnvelope,
    SpanKind,
    TelemetryReference,
    TraceSpan,
    Window,
)

# C2 — anomaly
from .anomaly import METRICS, AnomalyOutput, Baseline, Thresholds

# C5 — ground truth
from .ground_truth import FaultClass, Incident

__all__ = [
    # C1
    "CallType",
    "Edge",
    "IncidentTelemetry",
    "InfraNode",
    "LogRecordEnvelope",
    "MetricPointEnvelope",
    "OrderingSource",
    "ServiceEdge",
    "SpanEnvelope",
    "SpanKind",
    "TelemetryReference",
    "TraceSpan",
    "Window",
    # C2
    "METRICS",
    "AnomalyOutput",
    "Baseline",
    "Thresholds",
    # C5
    "FaultClass",
    "Incident",
]
