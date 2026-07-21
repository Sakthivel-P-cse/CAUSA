"""Shared cross-dev contracts (C1–C5).

stdlib-only. Lanes import types FROM here; never the reverse.
Changes to this package require all-three agreement.
"""

from __future__ import annotations

from contracts.c1_telemetry import LogRecord, LogsPort, TelemetryAdapters
from contracts.c2_anomaly import (
    AnomalyBag,
    AnomalyOutput,
    AnomalyThresholds,
    NodeAnomaly,
    Thresholds,
)
from contracts.c3_candidates import (
    ABSTENTION_FLAG,
    CLASSIFICATION_TARGETS,
    FaultClass,
    NodeKind,
    NodeRole,
    RankedCluster,
    RankedResult,
    Role,
    RootCauseCandidate,
    SignalScores,
)
from contracts.c4_scorer import (
    EnabledSignals,
    Scorer,
    Weights,
    normalize_enabled,
    normalize_weights,
)
from contracts.c5_ground_truth import DualInjectionGroundTruth, Incident, ScenarioId

__all__ = [
    "ABSTENTION_FLAG",
    "AnomalyBag",
    "AnomalyOutput",
    "AnomalyThresholds",
    "CLASSIFICATION_TARGETS",
    "DualInjectionGroundTruth",
    "EnabledSignals",
    "FaultClass",
    "Incident",
    "LogRecord",
    "LogsPort",
    "NodeAnomaly",
    "NodeKind",
    "NodeRole",
    "RankedCluster",
    "RankedResult",
    "Role",
    "RootCauseCandidate",
    "ScenarioId",
    "Scorer",
    "SignalScores",
    "TelemetryAdapters",
    "Thresholds",
    "Weights",
    "normalize_enabled",
    "normalize_weights",
]
