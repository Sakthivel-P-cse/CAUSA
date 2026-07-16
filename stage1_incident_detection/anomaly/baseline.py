"""
baseline.py — robust healthy-baseline learning (RCA_Engine_Spec §1.1).

Per (node, metric), learn robust statistics from a healthy warmup window:
    median μ_m,  mad s_m = 1.4826 * median(|x - μ|)

Robust median/MAD (not mean/std) is the whole point: a spike inside the warmup
window — or the fault itself leaking in — barely moves μ/s. This is load-bearing
for T, D, and P simultaneously (Team_Split §7.1), so it is the highest-care code
in the lane.

The scorer NEVER sees injected truth: the baseline is learned before/around a
healthy window and reused across all incidents.
"""

from __future__ import annotations

import logging
import math
import statistics
from typing import TYPE_CHECKING, Iterable

from contracts import METRICS, Baseline, Window

if TYPE_CHECKING:
    from ingest.adapters import TelemetryAdapter

logger = logging.getLogger(__name__)

# A single SMALL absolute ε floor, per spec §1.1 and the C2 contract docstring
# ("floored at a small ε"): it only stops `s_m + ε` from dividing by ~zero when
# the healthy series is perfectly flat. It is deliberately NOT a fraction of the
# median — a relative floor (e.g. 5% of median) silently halves sensitivity, so a
# genuine ~10% jump on a tight-healthy metric never fires. Robustness comes from
# the median/MAD, not from inflating the spread.
_MAD_ABS_FLOOR = 1e-6


def robust_baseline(values: Iterable[float]) -> Baseline | None:
    """Compute a Baseline (median, scaled+floored MAD, n) from raw samples.

    Non-finite samples (NaN, ±inf) are dropped before any statistic is taken: a
    single NaN would make median/MAD NaN and silently disable the metric, and a
    +inf would make it always-anomalous. Garbage in must not poison the baseline.
    """
    xs: list[float] = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            xs.append(f)
    if not xs:
        return None
    median = statistics.median(xs)
    mad_raw = statistics.median([abs(x - median) for x in xs])
    mad = max(1.4826 * mad_raw, _MAD_ABS_FLOOR)
    return Baseline(median=median, mad=mad, n=len(xs))


def capture_warmup(
    adapter: "TelemetryAdapter",
    nodes: Iterable[str],
    window: Window,
    *,
    metrics: tuple[str, ...] = METRICS,
    namespace: str | None = None,
) -> dict[tuple[str, str], Baseline]:
    """
    Learn baselines for every (node, metric) over a healthy `window`.

    Metrics with no data for a node are simply omitted (a node/metric with no
    baseline contributes nothing to its anomaly_score — max-composite tolerates
    that, and the gap is measured downstream as subgraph recall, not hidden).
    """
    baselines: dict[tuple[str, str], Baseline] = {}
    for node in nodes:
        for metric in metrics:
            series = adapter.metrics(node, metric, window, namespace=namespace)
            if not series:
                continue
            b = robust_baseline(v[1] for v in series)
            if b is not None:
                baselines[(node, metric)] = b
    logger.info(
        "Captured warmup baselines for %d (node, metric) pairs", len(baselines)
    )
    return baselines
