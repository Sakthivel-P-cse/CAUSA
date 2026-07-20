"""Fixture S2 — retry storm / backpressure (edge orientation, signal D).

Derived from testbed/chaos/S2_retry_storm_delay.yaml, whose CRD labels are the
ground truth: `causa/true-root: product-catalog`, `causa/fault-class: Dependency`.
NetworkChaos delays traffic TO product-catalog, so its callers retry and
saturation propagates UPSTREAM, against the call arrow.

Topology (call direction; the IG reverses sync edges):

  frontend ──sync──► product-catalog   ◄──sync── recommendation
     │  │                                             ▲
     │  └──────────────sync─────────────────────────-─┘
     └──sync──► cart      (unaffected by the delay)

Timeline — the delayed callee deviates FIRST, callers follow as retries build:
  product-catalog  FirstDeviation=0.0   anomaly=0.68   ← TRUE ROOT
  recommendation   FirstDeviation=5.0   anomaly=0.88
  frontend         FirstDeviation=9.0   anomaly=0.91
  cart             FirstDeviation=None  anomaly=0.12   (healthy neighbour)

NOTE ON ANTI-CIRCULARITY (PRD2 §6). The true root deliberately carries the
THIRD-highest anomaly, below both of its callers. That is what a retry storm
does: the caller burns connections and errors out while the dependency is merely
slow, so raw magnitude points at the wrong node. Ranking the root first requires
temporal primacy (it moved first) and edge orientation (after sync reversal,
influence flows out of it) — which is precisely the mechanism S2 exists to test.
Handing the root the top anomaly score would make the fixture pass without
exercising anything.

`cart` is present because a real incident window contains healthy neighbours;
it enters the subgraph through connectivity expansion, not on its own evidence.

TWO OPEN QUESTIONS, recorded rather than tuned away:

1. This fixture does NOT isolate D. Re-scoring it with the sync edges left
   un-reversed still ranks product-catalog first (0.387 vs 0.333), because
   T=1.0 alone carries it; orientation collapses the margin from 0.297 to
   0.054 but does not change the rank. The CRD header calls S2 "edge
   orientation / D", and in its natural form temporal primacy is sufficient.
   Making D decisive would mean fiddling the onsets to prove a mechanism
   works — the same circularity the anti-circularity rule forbids, inverted.

2. The onset ordering rests on an unverified telemetry assumption. `p95_latency`
   comes from `traces_service_graph_request_server_seconds{server=...}` — a
   SERVER-side duration — while the CRD delays traffic `direction: to`. Network
   delay toward a service need not raise its own processing time, so the callers
   may well deviate first and product-catalog barely at all. If that is what a
   real run shows, this fixture's timeline inverts and D (not T) becomes the
   only signal that can find the root. Settling it requires a chaos run against
   a live cluster; it cannot be reasoned out from the CRD alone.
"""

from __future__ import annotations

from contracts import CallType, NodeKind
from graphs.types import Edge, Node, Span, Topology, Trace
from rca.types import KernelAnomaly

from .types import FixtureExpectation


def load_incident_s2() -> tuple[
    Topology, tuple[Trace, ...], KernelAnomaly, FixtureExpectation
]:
    nodes = (
        Node("frontend", NodeKind.SERVICE, "frontend"),
        Node("recommendation", NodeKind.SERVICE, "recommendation"),
        # An instrumented demo service, not a backing store — the fault CLASS is
        # Dependency (something upstream depends on it) while the node KIND is
        # SERVICE. The two senses of "dependency" are deliberately distinct.
        Node("product-catalog", NodeKind.SERVICE, "product-catalog"),
        Node("cart", NodeKind.SERVICE, "cart"),
    )

    edges = (
        Edge("frontend", "product-catalog", CallType.SYNC, degraded=True),
        Edge("recommendation", "product-catalog", CallType.SYNC, degraded=True),
        Edge("frontend", "recommendation", CallType.SYNC, degraded=True),
        Edge("frontend", "cart", CallType.SYNC, degraded=False),
    )

    topology = Topology(nodes=nodes, edges=edges)

    # Durations reflect the injected 500ms delay surfacing as caller-side waits.
    spans = (
        Span("t1", "frontend", None, 0.0, 1400.0, "SERVER"),
        Span("t2", "recommendation", "t1", 20.0, 1200.0, "CLIENT"),
        Span("t3", "product-catalog", "t2", 40.0, 620.0, "SERVER"),
        Span("t4", "product-catalog", "t1", 60.0, 610.0, "SERVER"),
        Span("t5", "cart", "t1", 30.0, 45.0, "CLIENT"),
    )
    traces = (Trace("trace-s2", spans, anomalous=True),)

    anomaly = KernelAnomaly(
        anomaly_score={
            "frontend": 0.91,          # retry storm: worst-LOOKING, not the cause
            "recommendation": 0.88,
            "product-catalog": 0.68,   # merely slow — the actual root
            "cart": 0.12,
        },
        first_deviation_time={
            "product-catalog": 0.0,    # delay lands here first
            "recommendation": 5.0,
            "frontend": 9.0,
            "cart": None,              # never deviated
        },
        resource_deviation={
            "frontend": True,          # connection/thread saturation from retries
            "recommendation": True,
            "product-catalog": False,  # latency, not resource exhaustion
            "cart": False,
        },
        anomalous_trace_ids=frozenset({"trace-s2"}),
        t0=0.0,
    )

    ground_truth = FixtureExpectation(
        incident_id="s2-retry-storm-delay",
        true_root_node="product-catalog",
        fault_class="Dependency",
        inject_start=0.0,
        inject_end=30.0,
        expected_cluster_count=1,
        origin_nodes=frozenset({"product-catalog"}),
    )
    return topology, traces, anomaly, ground_truth
