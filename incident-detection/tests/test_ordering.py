"""
Causality-safe ordering (S3 clock-skew defense, PRD2 §4 / spec §1.1).

Even when a child span's pod clock is skewed EARLIER than its parent's, the DAG
depth must follow parent/child linkage — not wall-clock — so relative causal
order survives. Parented spans are tagged OrderingSource.TRACE_DAG.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ingest.ordering import enrich_spans_with_dag
from contracts import OrderingSource, SpanKind, TraceSpan


def _span(span_id, parent, start):
    return TraceSpan(
        span_id=span_id, parent_span_id=parent, service="svc", operation="op",
        duration_ms=1.0, status_code=1, error_message=None, start_time=start,
        kind=SpanKind.INTERNAL, attributes={},
    )


def test_dag_depth_survives_clock_skew():
    t0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    root = _span("root", None, t0)
    # child's clock is skewed 30s EARLIER than its parent (S3-style)
    child = _span("child", "root", t0 - timedelta(seconds=30))
    grandchild = _span("gc", "child", t0 + timedelta(seconds=5))

    envelopes = {e.span.span_id: e for e in enrich_spans_with_dag([root, child, grandchild], "tid")}

    # Depth follows the DAG, not the (skewed) wall clock.
    assert envelopes["root"].dag_depth == 0
    assert envelopes["child"].dag_depth == 1
    assert envelopes["gc"].dag_depth == 2

    # Parented spans are ordered by the trace DAG.
    assert envelopes["child"].ordering_source is OrderingSource.TRACE_DAG
    assert envelopes["gc"].ordering_source is OrderingSource.TRACE_DAG
    # The root has no parent → wall-clock tier.
    assert envelopes["root"].ordering_source is OrderingSource.WALL_CLOCK


def test_all_spans_enriched():
    t0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    spans = [_span("a", None, t0), _span("b", "a", t0), _span("c", "a", t0)]
    envelopes = enrich_spans_with_dag(spans, "tid")
    assert len(envelopes) == 3
    assert all(e.trace_id == "tid" for e in envelopes)


def test_output_list_is_topologically_ordered():
    # Audit M6: the returned LIST (not just dag_depth) must place every parent
    # before its children. Feed them in reverse (grandchild, child, root) — the
    # exact counterexample that used to emit child-before-parent.
    t0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    grandchild = _span("gc", "child", t0 + timedelta(seconds=5))
    child = _span("child", "root", t0 - timedelta(seconds=30))   # skewed earlier
    root = _span("root", None, t0)

    order = [e.span.span_id for e in enrich_spans_with_dag([grandchild, child, root], "tid")]
    assert order.index("root") < order.index("child") < order.index("gc")


def test_depth_of_survives_a_cycle():
    # Malformed data: a self-referential parent and a 2-cycle must not hang or
    # RecursionError; they degrade to depth 0 (audit L3).
    t0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
    self_ref = _span("s", "s", t0)
    a = _span("a", "b", t0)
    b = _span("b", "a", t0)
    envs = {e.span.span_id: e for e in enrich_spans_with_dag([self_ref, a, b], "tid")}
    assert len(envs) == 3
    assert all(e.dag_depth is not None and e.dag_depth >= 0 for e in envs.values())
