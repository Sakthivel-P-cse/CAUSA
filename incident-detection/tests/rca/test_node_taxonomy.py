"""
NodeKind taxonomy: SERVICE / DEPENDENCY / INFRA must stay structurally distinct.

INFRA was previously used loosely for "not a service", so a database was typed
INFRA in some fixtures and DEPENDENCY in others (S1 and S5 disagreed about the
same `catalog-db`), and `adapt_incident` emitted only INFRA/SERVICE — DEPENDENCY
never appeared on live data at all. The layers are causally different: a
saturated HOST explains every workload on it, a degraded DEPENDENCY explains
only its callers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from contracts import (
    AnomalyOutput,
    CallType,
    Edge,
    IncidentTelemetry,
    InfraNode,
    NodeKind,
    ServiceEdge,
    SpanEnvelope,
    TraceSpan,
)
from rca.adapter import adapt_incident

START = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
END = START + timedelta(minutes=10)


def _infra(name, kind, *, host_node=None, service_name=None):
    return InfraNode(
        name=name, kind=kind, namespace=None,
        cpu_usage_pct=None, memory_usage_pct=None,
        restart_count=None, condition=None,
        host_node=host_node, service_name=service_name,
    )


def _span(service, span_id="s1", parent=None):
    return SpanEnvelope(
        span=TraceSpan(
            span_id=span_id, parent_span_id=parent, service=service,
            operation="op", duration_ms=1.0, status_code=0,
            error_message=None, start_time=START,
        ),
        trace_id="t1",
    )


def _telemetry(**kw):
    base = dict(
        incident_id="inc-1", alerted_service="frontend",
        window_start=START, window_end=END,
    )
    base.update(kw)
    return IncidentTelemetry(**base)


def _kinds(telemetry, anomalies=()):
    adapted = adapt_incident(telemetry, anomalies)
    return {n.node_id: n.kind for n in adapted.topology.nodes}


# ── adapter classification ────────────────────────────────────────────


def test_uninstrumented_sink_is_a_dependency():
    """Called, emits no spans of its own, originates nothing → DEPENDENCY."""
    kinds = _kinds(_telemetry(
        spans=[_span("frontend")],
        service_edges=[ServiceEdge("frontend", "cart-db", 1.0, 0.0, 5.0)],
    ))
    assert kinds["cart-db"] is NodeKind.DEPENDENCY
    assert kinds["frontend"] is NodeKind.SERVICE


def test_span_emitter_is_a_service_even_as_a_pure_callee():
    """A leaf SERVICE is instrumented; it must not be demoted to DEPENDENCY."""
    kinds = _kinds(_telemetry(
        spans=[_span("frontend"), _span("leaf-svc", span_id="s2", parent="s1")],
        service_edges=[ServiceEdge("frontend", "leaf-svc", 1.0, 0.0, 5.0)],
    ))
    assert kinds["leaf-svc"] is NodeKind.SERVICE


def test_caller_without_spans_is_still_a_service():
    """Originating calls is evidence of being a workload, spans or not."""
    kinds = _kinds(_telemetry(
        service_edges=[
            ServiceEdge("gateway", "orders", 1.0, 0.0, 5.0),
            ServiceEdge("orders", "orders-db", 1.0, 0.0, 5.0),
        ],
    ))
    assert kinds["gateway"] is NodeKind.SERVICE
    assert kinds["orders"] is NodeKind.SERVICE
    assert kinds["orders-db"] is NodeKind.DEPENDENCY


def test_only_hosts_are_infra_pods_are_not():
    """get_infra_nodes returns pod rows too; those must not become INFRA
    vertices — a pod is where a workload runs, not a layer of its own."""
    kinds = _kinds(_telemetry(
        spans=[_span("cart")],
        infra_nodes=[
            _infra("kind-worker", "node"),
            _infra("cart-abc123", "pod", host_node="kind-worker", service_name="cart"),
        ],
        edges=[Edge("kind-worker", "cart", CallType.RUNS_ON)],
    ))
    assert kinds["kind-worker"] is NodeKind.INFRA
    assert kinds["cart"] is NodeKind.SERVICE
    assert "cart-abc123" not in kinds, "pod names must not enter the graph"


def test_host_and_dependency_coexist_without_collapsing():
    kinds = _kinds(_telemetry(
        spans=[_span("cart")],
        service_edges=[ServiceEdge("cart", "cart-db", 1.0, 0.0, 5.0)],
        infra_nodes=[_infra("kind-worker", "node")],
        edges=[
            Edge("kind-worker", "cart", CallType.RUNS_ON),
            Edge("cart", "cart-db", CallType.SYNC),
        ],
    ))
    assert kinds["kind-worker"] is NodeKind.INFRA
    assert kinds["cart-db"] is NodeKind.DEPENDENCY
    assert kinds["cart"] is NodeKind.SERVICE


def test_node_with_no_call_evidence_defaults_to_service():
    """A node known only from an anomaly row is never called by anything in the
    telemetry, so there is no evidence it is a sink. Silence must not be read as
    proof of dependency-hood — that would mislabel the fault class."""
    anomalies = [AnomalyOutput(node="mystery", anomaly_score=0.9, dev_vector={})]
    kinds = _kinds(_telemetry(spans=[_span("frontend")]), anomalies)
    assert kinds["mystery"] is NodeKind.SERVICE


def test_dependency_requires_being_called_not_merely_silent():
    """The discriminator is `is called AND neither emits nor calls` — not the
    weaker `absent from emitters and callers`, which catches unrelated nodes."""
    telemetry = _telemetry(
        spans=[_span("frontend")],
        service_edges=[ServiceEdge("frontend", "cart-db", 1.0, 0.0, 5.0)],
    )
    anomalies = [AnomalyOutput(node="orphan", anomaly_score=0.5, dev_vector={})]
    kinds = _kinds(telemetry, anomalies)
    assert kinds["cart-db"] is NodeKind.DEPENDENCY   # called → real sink
    assert kinds["orphan"] is NodeKind.SERVICE       # uncalled → no evidence


# ── fixture consistency ───────────────────────────────────────────────


ALL_FIXTURES = [
    "incident_s0", "incident_s1", "incident_s5",
    "incident_d1", "incident_d2", "incident_d3",
]


def _load(mod_name):
    import importlib

    mod = importlib.import_module(f"fixtures.{mod_name}")
    loader = next(
        getattr(mod, n) for n in dir(mod) if n.startswith("load_incident")
    )
    return loader()


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_infra_nodes_in_fixtures_are_hosts(fixture):
    """Every INFRA node must source a runs-on edge. This is the invariant that
    S0/S1 broke by typing a database INFRA."""
    topology, _, _, _ = _load(fixture)
    hosts = {
        e.src for e in topology.edges if e.call_type is CallType.RUNS_ON
    }
    for node in topology.nodes:
        if node.kind is NodeKind.INFRA:
            assert node.node_id in hosts, (
                f"{fixture}: {node.node_id} is typed INFRA but hosts nothing — "
                f"a store or queue should be DEPENDENCY"
            )


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_runs_on_targets_are_never_infra(fixture):
    """The thing running ON a host is a workload, not another host."""
    topology, _, _, _ = _load(fixture)
    by_id = {n.node_id: n for n in topology.nodes}
    for e in topology.edges:
        if e.call_type is CallType.RUNS_ON and e.dst in by_id:
            assert by_id[e.dst].kind is not NodeKind.INFRA, (
                f"{fixture}: {e.dst} runs on {e.src} but is typed INFRA"
            )


def test_same_named_node_agrees_across_fixtures():
    """S1 typed `catalog-db` INFRA while S5 typed it DEPENDENCY."""
    seen: dict[str, set] = {}
    for fixture in ALL_FIXTURES:
        topology, _, _, _ = _load(fixture)
        for node in topology.nodes:
            seen.setdefault(node.node_id, set()).add(node.kind)
    conflicts = {k: v for k, v in seen.items() if len(v) > 1}
    assert not conflicts, f"same node typed inconsistently across fixtures: {conflicts}"
