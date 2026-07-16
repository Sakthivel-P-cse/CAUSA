"""
score.py — anomaly_score + dev_vector (RCA_Engine_Spec §1.1).

    z_m(t)   = max(0, (x_m(t) - μ_m) / (s_m + ε))     # one-sided: only increases
    dev_m(t) = 1 - exp(-z_m(t) / κ)                    # squash to [0,1]
    anomaly_score(node,t) = max over m of dev_m(t)     # max, not weighted sum

Max-composite so one sharply-broken metric isn't diluted by healthy ones (a
deadlock spikes latency while CPU looks fine). dev_vector is retained for Dev C's
classification + explainability.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Mapping

from contracts import METRICS, AnomalyOutput, Baseline, Thresholds, Window

if TYPE_CHECKING:
    from ingest.adapters import TelemetryAdapter

_EPS = 1e-9


def dev(value: float, baseline: Baseline, kappa: float) -> float:
    """Per-metric one-sided squashed deviation ∈ [0,1]."""
    z = max(0.0, (value - baseline.median) / (baseline.mad + _EPS))
    return 1.0 - math.exp(-z / kappa)


def dev_vector(
    values: Mapping[str, float],
    baselines: Mapping[tuple[str, str], Baseline],
    node: str,
    kappa: float,
) -> dict[str, float]:
    """{metric: dev_m} for the metrics that have both a value and a baseline."""
    out: dict[str, float] = {}
    for metric, value in values.items():
        b = baselines.get((node, metric))
        if b is not None:
            out[metric] = dev(value, b, kappa)
    return out


def score_values(
    node: str,
    values: Mapping[str, float],
    baselines: Mapping[tuple[str, str], Baseline],
    thresholds: Thresholds,
    window_ts: datetime | None = None,
) -> AnomalyOutput:
    """anomaly_score (max-composite) + dev_vector from a metric→value snapshot."""
    dv = dev_vector(values, baselines, node, thresholds.kappa)
    score = max(dv.values(), default=0.0)
    return AnomalyOutput(node=node, anomaly_score=score, dev_vector=dv, window_ts=window_ts)


def anomaly_series(
    node: str,
    window: Window,
    adapter: "TelemetryAdapter",
    baselines: Mapping[tuple[str, str], Baseline],
    thresholds: Thresholds,
    *,
    metrics: tuple[str, ...] = METRICS,
    namespace: str | None = None,
) -> list[AnomalyOutput]:
    """
    Per-timestamp anomaly_score over `window`, aligned across metrics on the
    shared window-derived step grid (adapters._step_seconds). Timestamps present
    in any metric are scored; a metric absent at a timestamp simply doesn't
    contribute there.
    """
    # metric → {ts: value}
    per_metric: dict[str, dict[float, float]] = {}
    all_ts: set[float] = set()
    for metric in metrics:
        if (node, metric) not in baselines:
            continue
        series = adapter.metrics(node, metric, window, namespace=namespace)
        if not series:
            continue
        m = {float(ts): float(v) for ts, v in series}
        per_metric[metric] = m
        all_ts.update(m.keys())

    out: list[AnomalyOutput] = []
    for ts in sorted(all_ts):
        values = {
            metric: m[ts] for metric, m in per_metric.items() if ts in m
        }
        out.append(
            score_values(
                node, values, baselines, thresholds,
                window_ts=datetime.fromtimestamp(ts, tz=timezone.utc),
            )
        )
    return out


def anomaly(
    node: str,
    window: Window,
    adapter: "TelemetryAdapter",
    baselines: Mapping[tuple[str, str], Baseline],
    thresholds: Thresholds,
    *,
    metrics: tuple[str, ...] = METRICS,
    namespace: str | None = None,
) -> AnomalyOutput:
    """The latest anomaly_score for a node in the window (contract C2)."""
    series = anomaly_series(
        node, window, adapter, baselines, thresholds,
        metrics=metrics, namespace=namespace,
    )
    if series:
        return series[-1]
    return AnomalyOutput(node=node, anomaly_score=0.0, dev_vector={}, window_ts=None)
