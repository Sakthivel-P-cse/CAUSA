"""
deviation.py — FirstDeviationTime + thresholds (RCA_Engine_Spec §1.1).

FirstDeviationTime(node) = the earliest window where anomaly_score crosses
θ_fire and STAYS above it for k_persist consecutive windows (persistence guards
against single-window blips). The returned timestamp is the ONSET — the first of
that run, not the k-th.

Returns None for a slow-burn node that never crosses θ_fire. That None is a
FEATURE, not a failure: a causal-but-slow origin (e.g. S1 Redis memory ramp)
legitimately never fires, and downstream this is measured as subgraph recall.
Never fake a timestamp (Dev_A brief, definition-of-done).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Mapping, Sequence

from contracts import METRICS, AnomalyOutput, Baseline, Thresholds, Window

from .score import anomaly_series

if TYPE_CHECKING:
    from ingest.adapters import TelemetryAdapter


def thresholds() -> Thresholds:
    """The spec's default firing/squash thresholds (contract C2)."""
    return Thresholds()


def first_deviation_from_scores(
    scored: Sequence[tuple[datetime, float]],
    th: Thresholds,
    max_gap_seconds: float | None = None,
) -> datetime | None:
    """
    Onset timestamp from a (timestamp, anomaly_score) sequence, or None if no run
    of k_persist consecutive windows stays above θ_fire.

    "Consecutive" is TIME-adjacent, not array-adjacent: a gap in the series
    (a missing window) breaks the run. Without this, two samples minutes apart
    across a hole count as neighbours and a gapped series can false-fire
    (audit). `max_gap_seconds` sets the adjacency tolerance; when omitted it is
    inferred as 1.5× the median inter-sample gap of the data.
    """
    scored = sorted(scored, key=lambda p: p[0])
    if max_gap_seconds is None and len(scored) >= 2:
        gaps = sorted(
            (scored[i][0] - scored[i - 1][0]).total_seconds()
            for i in range(1, len(scored))
        )
        median_gap = gaps[len(gaps) // 2]
        max_gap_seconds = median_gap * 1.5 if median_gap > 0 else None

    run_start: datetime | None = None
    run_len = 0
    prev_ts: datetime | None = None
    for ts, score in scored:
        # A non-adjacent sample (gap > tolerance) resets any run in progress.
        if (
            prev_ts is not None
            and max_gap_seconds is not None
            and (ts - prev_ts).total_seconds() > max_gap_seconds
        ):
            run_len = 0
            run_start = None
        if score > th.theta_fire:
            if run_len == 0:
                run_start = ts
            run_len += 1
            if run_len >= th.k_persist:
                return run_start
        else:
            run_len = 0
            run_start = None
        prev_ts = ts
    return None


def first_deviation(
    node: str,
    window: Window,
    adapter: "TelemetryAdapter",
    baselines: Mapping[tuple[str, str], Baseline],
    th: Thresholds | None = None,
    *,
    metrics: tuple[str, ...] = METRICS,
    namespace: str | None = None,
) -> datetime | None:
    """FirstDeviationTime for a node over `window` (None = slow-burn)."""
    th = th or thresholds()
    series: list[AnomalyOutput] = anomaly_series(
        node, window, adapter, baselines, th, metrics=metrics, namespace=namespace
    )
    scored = [(o.window_ts, o.anomaly_score) for o in series if o.window_ts is not None]
    return first_deviation_from_scores(scored, th)


def node_anomaly(
    node: str,
    window: Window,
    adapter: "TelemetryAdapter",
    baselines: Mapping[tuple[str, str], Baseline],
    th: Thresholds | None = None,
    *,
    metrics: tuple[str, ...] = METRICS,
    namespace: str | None = None,
) -> AnomalyOutput:
    """
    The full C2 per-node summary Dev B consumes: the PEAK anomaly_score in the
    window (the worst deviation, not the last sample), its dev_vector, and the
    node's FirstDeviationTime (`first_deviation`, None for a slow-burn origin).
    Computes the series once and derives all three from it.
    """
    th = th or thresholds()
    series: list[AnomalyOutput] = anomaly_series(
        node, window, adapter, baselines, th, metrics=metrics, namespace=namespace
    )
    scored = [(o.window_ts, o.anomaly_score) for o in series if o.window_ts is not None]
    fdt = first_deviation_from_scores(scored, th)
    if not series:
        return AnomalyOutput(
            node=node, anomaly_score=0.0, dev_vector={},
            first_deviation=None, window_ts=None,
        )
    peak = max(series, key=lambda o: o.anomaly_score)
    return AnomalyOutput(
        node=node,
        anomaly_score=peak.anomaly_score,
        dev_vector=peak.dev_vector,
        first_deviation=fdt,
        window_ts=peak.window_ts,
    )
