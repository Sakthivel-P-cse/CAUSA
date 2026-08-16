"""
FirstDeviationTime — onset detection, slow-burn null, blip guard (spec §1.1).
"""

from __future__ import annotations

from datetime import datetime, timezone

from anomaly import first_deviation, thresholds
from anomaly.deviation import first_deviation_from_scores
from contracts import Baseline, Thresholds, Window


def _win(base_unix):
    return Window(
        datetime.fromtimestamp(base_unix, tz=timezone.utc),
        datetime.fromtimestamp(base_unix + 600, tz=timezone.utc),
    )


def test_sharp_node_onset(fake_adapter_cls, base_unix):
    # healthy for 4 windows, then jumps and stays → onset at window 4
    series = [[base_unix + i * 60, 0.0] for i in range(4)] + \
             [[base_unix + i * 60, 0.5] for i in range(4, 10)]
    adapter = fake_adapter_cls({("cart", "error_rate"): series})
    base = {("cart", "error_rate"): Baseline(0.0, 1e-6, 50)}
    fd = first_deviation("cart", _win(base_unix), adapter, base, thresholds())
    assert fd == datetime.fromtimestamp(base_unix + 4 * 60, tz=timezone.utc)


def test_slow_burn_returns_none(fake_adapter_cls, base_unix):
    # slowly rising latency that never crosses theta_fire
    series = [[base_unix + i * 60, 100.0 + i * 0.5] for i in range(10)]
    adapter = fake_adapter_cls({("cart", "p95_latency"): series})
    base = {("cart", "p95_latency"): Baseline(100.0, 5.0, 50)}
    assert first_deviation("cart", _win(base_unix), adapter, base, thresholds()) is None


def test_single_blip_guarded_by_k_persist(fake_adapter_cls, base_unix):
    series = [[base_unix + i * 60, 0.0] for i in range(5)] + \
             [[base_unix + 5 * 60, 0.9]] + \
             [[base_unix + i * 60, 0.0] for i in range(6, 10)]
    adapter = fake_adapter_cls({("cart", "error_rate"): series})
    base = {("cart", "error_rate"): Baseline(0.0, 1e-6, 50)}
    assert first_deviation("cart", _win(base_unix), adapter, base, thresholds()) is None


def test_from_scores_pure():
    th = Thresholds()
    t = [datetime.fromtimestamp(1000 + i * 60, tz=timezone.utc) for i in range(6)]
    scored = [(t[0], 0.1), (t[1], 0.9), (t[2], 0.9), (t[3], 0.9), (t[4], 0.1), (t[5], 0.9)]
    # first run of 3 consecutive >theta starts at t[1]
    assert first_deviation_from_scores(scored, th) == t[1]


def test_from_scores_no_run():
    th = Thresholds()
    t = [datetime.fromtimestamp(1000 + i * 60, tz=timezone.utc) for i in range(4)]
    scored = [(t[0], 0.9), (t[1], 0.1), (t[2], 0.9), (t[3], 0.1)]
    assert first_deviation_from_scores(scored, th) is None
