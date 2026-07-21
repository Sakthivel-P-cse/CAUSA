"""
contracts/ — the Causa cross-dev seam (C1 telemetry, C2 anomaly, C5 ground truth).

This package depends on the stdlib ONLY. Dev A produces these types; Dev B and
Dev C consume them. Changing anything here requires all three devs' approval
(Team_Split §3). Import the types from here — never from a client or a reader.
"""

from __future__ import annotations

from .telemetry import (
    CallType,
    Edge,
    IncidentTelemetry,
    InfraNode,
    LogRecord,
    LogRecordEnvelope,
    LogsPort,
    MetricPointEnvelope,
    OrderingSource,
    ServiceEdge,
    SpanEnvelope,
    SpanKind,
    TelemetryAdapters,
    TelemetryReference,
    TraceSpan,
    Window,
)

from .anomaly import (
    METRICS,
    AnomalyBag,
    AnomalyOutput,
    AnomalyThresholds,
    Baseline,
    NodeAnomaly,
    Thresholds,
)

from .rca import (
    ABSTENTION_FLAG,
    NodeKind,
    NodeRole,
    RankedCluster,
    RankedResult,
    Role,
    RootCauseCandidate,
    SignalScores,
)

from .scorer_port import (
    EnabledSignals,
    Scorer,
    Weights,
    normalize_enabled,
    normalize_weights,
)

from .ground_truth import (
    CLASSIFICATION_TARGETS,
    DualInjectionGroundTruth,
    FaultClass,
    Incident,
    ScenarioId,
)

__all__ = [
    "CallType",
    "Edge",
    "IncidentTelemetry",
    "InfraNode",
    "LogRecord",
    "LogRecordEnvelope",
    "LogsPort",
    "MetricPointEnvelope",
    "OrderingSource",
    "ServiceEdge",
    "SpanEnvelope",
    "SpanKind",
    "TelemetryAdapters",
    "TelemetryReference",
    "TraceSpan",
    "Window",
    "METRICS",
    "AnomalyBag",
    "AnomalyOutput",
    "AnomalyThresholds",
    "Baseline",
    "NodeAnomaly",
    "Thresholds",
    "ABSTENTION_FLAG",
    "NodeKind",
    "NodeRole",
    "RankedCluster",
    "RankedResult",
    "Role",
    "RootCauseCandidate",
    "SignalScores",
    "EnabledSignals",
    "Scorer",
    "Weights",
    "normalize_enabled",
    "normalize_weights",
    "CLASSIFICATION_TARGETS",
    "DualInjectionGroundTruth",
    "FaultClass",
    "Incident",
    "ScenarioId",
]
