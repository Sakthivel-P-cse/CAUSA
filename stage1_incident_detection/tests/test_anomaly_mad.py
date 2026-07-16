"""
Anomaly baseline robustness + max-composite (RCA_Engine_Spec §1.1).

The DoD spot-check: a spike injected into the warmup window must NOT move
median/MAD (robust stats). And anomaly_score must be max-composite so a single
broken metric isn't diluted by healthy ones.
"""

from __future__ import annotations

from datetime import datetime, timezone

from anomaly import anomaly, robust_baseline, thresholds
from anomaly.score import dev
from contracts import Baseline, Window


def test_mad_robust_to_warmup_spike():
    healthy = [100.0] * 40 + [99.0, 101.0] * 5
    b0 = robust_baseline(healthy)
    b1 = robust_baseline(healthy + [100_000.0])  # one huge spike
    assert abs(b0.median - b1.median) < 1.0
    assert abs(b0.mad - b1.mad) < 1.0


def test_mad_floor_prevents_zero_spread():
    # A perfectly flat healthy series would give MAD=0; the floor must keep it > 0
    # so z doesn't explode / divide-by-zero.
    b = robust_baseline([0.0] * 50)
    assert b.mad > 0.0


def test_anomaly_score_in_unit_interval():
    b = Baseline(median=100.0, mad=5.0, n=50)
    for x in (50.0, 100.0, 130.0, 1_000.0):
        d = dev(x, b, kappa=3.0)
        assert 0.0 <= d <= 1.0
    # one-sided: a value BELOW the median is not anomalous
    assert dev(10.0, b, kappa=3.0) == 0.0


def test_max_composite_not_diluted(fake_adapter_cls, base_unix):
    # latency healthy, error spikes → score tracks the error, not the average
    win = Window(
        datetime.fromtimestamp(base_unix, tz=timezone.utc),
        datetime.fromtimestamp(base_unix + 600, tz=timezone.utc),
    )
    adapter = fake_adapter_cls({
        ("cart", "p95_latency"): [[base_unix + 540, 100.0]],
        ("cart", "error_rate"): [[base_unix + 540, 0.9]],
    })
    base = {
        ("cart", "error_rate"): Baseline(0.0, 1e-6, 50),
        ("cart", "p95_latency"): Baseline(100.0, 5.0, 50),
    }
    out = anomaly("cart", win, adapter, base, thresholds())
    assert out.anomaly_score > 0.9
    assert out.dev_vector["p95_latency"] < 0.1  # latency stayed healthy
