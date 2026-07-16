"""
anomaly/ — Causa Dev-A contract C2: the anomaly primitives.

The foundational per-node deviation layer that ALL scoring (T, D, P) sits on
(RCA_Engine_Spec §1.1). Consumes the C1 `metrics()` adapter, produces
AnomalyOutput + FirstDeviationTime. Imports only `contracts` and `ingest`.

    from anomaly import capture_warmup, anomaly, first_deviation, thresholds
    from anomaly import save_baselines, load_baselines

    base = capture_warmup(adapter, nodes, healthy_window, namespace="otel-demo")
    out  = anomaly("cartservice", incident_window, adapter, base, thresholds())
    t0   = first_deviation("cartservice", incident_window, adapter, base)  # None = slow-burn
"""

from __future__ import annotations

from contracts import METRICS, AnomalyOutput, Baseline, Thresholds

from .baseline import capture_warmup, robust_baseline
from .deviation import (
    first_deviation,
    first_deviation_from_scores,
    node_anomaly,
    thresholds,
)
from .score import anomaly, anomaly_series, dev, dev_vector, score_values
from .store import load_baselines, save_baselines

__all__ = [
    # contract re-exports
    "METRICS",
    "AnomalyOutput",
    "Baseline",
    "Thresholds",
    # baseline learning
    "capture_warmup",
    "robust_baseline",
    # scoring
    "anomaly",
    "anomaly_series",
    "dev",
    "dev_vector",
    "score_values",
    # deviation
    "first_deviation",
    "first_deviation_from_scores",
    "node_anomaly",
    "thresholds",
    # persistence
    "save_baselines",
    "load_baselines",
]
