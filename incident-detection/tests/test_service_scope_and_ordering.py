"""
Service scoping (stale names) + ordering tiers (dead-tier + FALLBACK).
"""

from __future__ import annotations

from datetime import datetime, timezone

from contracts import OrderingSource, TraceSpan
from ingest.ordering import enrich_spans_with_dag
from ingest.service_scope import (
    extract_service_from_labels,
    is_monitored_service,
    normalize_service_name,
)


def test_scope_excludes_the_real_load_generator():
    assert not is_monitored_service("load-generator")     # otel-demo name now excluded
    assert not is_monitored_service("loadgenerator")      # Boutique name still excluded


def test_scope_keeps_the_cart_cache_in_scope():
    # valkey-cart is S1's true root — it must NOT be excluded.
    assert is_monitored_service("valkey-cart")
    assert is_monitored_service("redis-cart")
    assert is_monitored_service("checkout")


def test_normalize_unknown_service_prefix():
    assert normalize_service_name("unknown_service:checkout") == "checkout"
    assert normalize_service_name("unknown_service:envoy") == "envoy"
    assert normalize_service_name("unknown_service:/nodejs/bin/node") == "unknown"
    assert normalize_service_name("/nodejs/bin/node") == "unknown"
    assert extract_service_from_labels({"service": "unknown_service:checkout"}) == "checkout"
    assert not is_monitored_service(normalize_service_name("unknown_service:/nodejs/bin/node"))


def test_system_infra_host_detection():
    from ingest.service_scope import is_system_infra_host

    assert is_system_infra_host("gcp-demo-test-control-plane")
    assert is_system_infra_host("kind-control-plane")
    assert is_system_infra_host("my-cluster-master")
    assert not is_system_infra_host("checkout")
    assert not is_system_infra_host("node-b")
    assert not is_system_infra_host("worker-1")


def _span(span_id, parent, t):
    return TraceSpan(span_id=span_id, parent_span_id=parent, service="s", operation="op",
                     duration_ms=1.0, status_code=1, error_message=None,
                     start_time=datetime.fromtimestamp(t, tz=timezone.utc))


def test_ordering_tiers_are_distinct():
    spans = [
        _span("root", None, 1000),          # true root → WALL_CLOCK
        _span("child", "root", 1001),       # parent present → TRACE_DAG
        _span("orphan", "ghost", 1002),     # parent absent from trace → FALLBACK
    ]
    env = {e.span.span_id: e for e in enrich_spans_with_dag(spans, "trace-1")}
    assert env["root"].ordering_source is OrderingSource.WALL_CLOCK
    assert env["child"].ordering_source is OrderingSource.TRACE_DAG
    assert env["orphan"].ordering_source is OrderingSource.FALLBACK
