"""
Anomaly hardening — MAD sensitivity (#3), NaN/inf guards, time-adjacent
persistence, node_anomaly summary, and NaN-free baseline persistence.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from anomaly import (
    anomaly,
    first_deviation_from_scores,
    load_baselines,
    node_anomaly,
    robust_baseline,
    save_baselines,
    thresholds,
)
from anomaly.score import dev
from contracts import Baseline, Window


def test_mad_floor_does_not_halve_sensitivity():
    # tight-but-real healthy spread; a genuine ~10% jump must fire high, not ~0.49
    # (which the old 5%-of-median relative floor produced).
    b = robust_baseline([100.0, 99.0, 101.0, 100.0, 98.0, 102.0] * 10)
    assert b.mad < 5.0                       # not inflated to 5% of the median
    assert dev(110.0, b, kappa=3.0) > 0.8


def test_robust_baseline_drops_nan_and_inf():
    b = robust_baseline(
        [100.0, float("nan"), 101.0, float("inf"), 99.0, 100.5, 98.5]
    )
    assert b is not None
    assert math.isfinite(b.median) and math.isfinite(b.mad)
    assert b.n == 5
    assert robust_baseline([float("nan"), float("inf")]) is None


def test_robust_baseline_rejects_too_few_samples():
    assert robust_baseline([100.0, 101.0, 99.0, 100.5]) is None
    assert robust_baseline([100.0] * 5) is not None


def test_first_deviation_is_time_adjacent():
    th = thresholds()
    base = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    # two above-fire samples, a big gap, then a sustained adjacent run of 3
    scored = [
        (base, 0.9), (base + timedelta(seconds=30), 0.9),          # pre-gap pair
        (base + timedelta(seconds=1800), 0.9),                     # after a 30-min hole
        (base + timedelta(seconds=1830), 0.9),
        (base + timedelta(seconds=1860), 0.9),
    ]
    onset = first_deviation_from_scores(scored, th)
    assert onset == base + timedelta(seconds=1800)                 # gap reset the pre-gap run


def test_node_anomaly_carries_first_deviation(fake_adapter_cls, base_unix):
    win = Window(datetime.fromtimestamp(base_unix, tz=timezone.utc),
                 datetime.fromtimestamp(base_unix + 600, tz=timezone.utc))
    series = [[base_unix + i * 30, 0.9] for i in range(5)]         # sustained error spike
    adapter = fake_adapter_cls({("cart", "error_rate"): series})
    base = {("cart", "error_rate"): Baseline(0.0, 1e-6, 50)}
    out = node_anomaly("cart", win, adapter, base, thresholds())
    assert out.anomaly_score > 0.9
    assert out.first_deviation is not None                         # crossed → onset set


def test_anomaly_reports_peak_not_latest(fake_adapter_cls, base_unix):
    # Audit M1: anomaly() must report the window's PEAK deviation, not its last
    # sample. A spike that recedes by the end of the window (last value healthy)
    # must still score high, and must equal node_anomaly()'s anomaly_score.
    win = Window(datetime.fromtimestamp(base_unix, tz=timezone.utc),
                 datetime.fromtimestamp(base_unix + 600, tz=timezone.utc))
    # error_rate spikes at t+90..150 then recovers to baseline by the last sample
    series = [
        [base_unix + 0, 0.0], [base_unix + 30, 0.0], [base_unix + 60, 0.0],
        [base_unix + 90, 0.9], [base_unix + 120, 0.95], [base_unix + 150, 0.9],
        [base_unix + 180, 0.0], [base_unix + 210, 0.0],   # receded — latest is healthy
    ]
    adapter = fake_adapter_cls({("cart", "error_rate"): series})
    base = {("cart", "error_rate"): Baseline(0.0, 1e-6, 50)}

    out = anomaly("cart", win, adapter, base, thresholds())
    assert out.anomaly_score > 0.9                         # peak, not the ~0 latest sample
    # anomaly() and node_anomaly() agree on the score by construction (shared peak()).
    summ = node_anomaly("cart", win, adapter, base, thresholds())
    assert out.anomaly_score == summ.anomaly_score


def test_save_baselines_rejects_non_finite(tmp_path):
    bad = {("cart", "cpu"): Baseline(median=float("nan"), mad=1.0, n=5)}
    with pytest.raises(ValueError):
        save_baselines(bad, tmp_path / "b.json")


def test_load_baselines_skips_non_finite(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"cart": {"cpu": {"median": float("inf"), "mad": 1.0, "n": 5},
                                      "mem": {"median": 1.0, "mad": 2.0, "n": 9}}}))
    loaded = load_baselines(p)
    assert ("cart", "cpu") not in loaded
    assert ("cart", "mem") in loaded
