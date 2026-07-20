"""
Infra anomaly scoring, warmup → incident, against a mocked Prometheus.

The unit pieces (node series, reader wiring, baseline kind) can each be right
while the seam between them is broken: dev_vector silently DROPS any metric with
no (node, metric) baseline, so an infra node warmed under the wrong `kind` scores
0.0 forever and layer arbitration — which needs both an anomaly score and a
FirstDeviationTime for the infra node — can never fire. This pins the whole path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import respx

from anomaly import capture_warmup
from anomaly.score import score_values
from contracts import Thresholds, Window
from ingest import TelemetryAdapter

HEALTHY_CPU = 20.0
SATURATED_CPU = 95.0

WARM_START = datetime(2026, 7, 16, 11, 0, tzinfo=timezone.utc)
WARM_END = WARM_START + timedelta(minutes=30)


def _range(values):
    return httpx.Response(
        200, json={"status": "success", "data": {"result": [{"values": values}]}}
    )


def _prom(cpu_value: float):
    """Mock returning node CPU at `cpu_value` and nothing else."""

    def responder(request):
        q = request.url.params.get("query", "")
        if "node_cpu_seconds_total" in q:
            return _range([[1000 + 60 * i, str(cpu_value)] for i in range(12)])
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    return responder


@respx.mock
def test_infra_node_scores_anomalous_against_its_warmed_baseline():
    # ── warmup over a healthy window ──────────────────────────────────
    respx.get("http://prom.local/api/v1/query_range").mock(
        side_effect=_prom(HEALTHY_CPU)
    )
    respx.get("http://prom.local/api/v1/query").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"result": []}}
        )
    )
    adapter = TelemetryAdapter.from_defaults(prometheus_url="http://prom.local")
    try:
        baselines = capture_warmup(
            adapter,
            ["kind-worker"],
            Window(WARM_START, WARM_END),
            metrics=("cpu", "saturation"),
            kind="infra",
        )
    finally:
        adapter.close()

    assert ("kind-worker", "cpu") in baselines, (
        "infra baseline must key on (node name, contract metric name) — without "
        "it dev_vector drops the metric and the node scores 0.0 forever"
    )

    # ── incident window: same node, saturated ─────────────────────────
    thresholds = Thresholds()
    healthy = score_values(
        "kind-worker", {"cpu": HEALTHY_CPU}, baselines, thresholds
    )
    saturated = score_values(
        "kind-worker", {"cpu": SATURATED_CPU}, baselines, thresholds
    )

    assert healthy.anomaly_score < thresholds.theta_fire
    assert saturated.anomaly_score >= thresholds.theta_fire, (
        f"a saturated node must fire (got {saturated.anomaly_score})"
    )
    assert "cpu" in saturated.dev_vector


@respx.mock
def test_service_kind_warmup_does_not_produce_infra_baselines():
    """The regression that made arbitration inert: warming a node name under
    kind='service' queries container PromQL, which returns nothing for a host."""
    respx.get("http://prom.local/api/v1/query_range").mock(
        side_effect=_prom(HEALTHY_CPU)
    )
    respx.get("http://prom.local/api/v1/query").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"result": []}}
        )
    )
    adapter = TelemetryAdapter.from_defaults(
        prometheus_url="http://prom.local", namespace="otel-demo"
    )
    try:
        service_kind = capture_warmup(
            adapter,
            ["kind-worker"],
            Window(WARM_START, WARM_END),
            metrics=("cpu",),
            namespace="otel-demo",
        )
    finally:
        adapter.close()

    assert ("kind-worker", "cpu") not in service_kind
