"""
ordering.py — causality-safe span ordering (PRD2 §4, RCA_Engine_Spec §1.1).

Assigns each span a causal position from the hierarchy:
    trace DAG  >  logical clock  >  wall-clock  >  fallback

Which tier fixed a span's order is recorded in `OrderingSource`:
  • TRACE_DAG   — the span's parent is present in this trace, so parent linkage
                  (skew-robust) places it. This survives S3 clock skew, where
                  one pod's wall-clock is deliberately shifted during a fault.
  • WALL_CLOCK  — a true root (no parent ref): its own start time is the only
                  signal available.
  • FALLBACK    — a DANGLING span: it names a parent that isn't in the fetched
                  trace, so its DAG position is degraded/untrustworthy.
  • LOGICAL_CLOCK — reserved for when spans carry Lamport/seqno or span links
                  (not emitted by the OTel Demo's default instrumentation).

The within-parent `logical_seq` is a wall-clock tiebreak among siblings; the
skew-robust guarantee comes from `dag_depth` (parent linkage), not from that
sibling ordering.
"""

from __future__ import annotations

from contracts import OrderingSource, SpanEnvelope, TelemetryReference, TraceSpan


def enrich_spans_with_dag(spans: list[TraceSpan], trace_id: str) -> list[SpanEnvelope]:
    """
    Attach causal DAG depth + sibling logical_seq to each span, and record which
    tier of the causality hierarchy fixed its order.

    depth 0 = root. logical_seq orders children of the same parent by wall time
    (a within-parent tiebreak, not a cross-trace clock).
    """
    by_id = {s.span_id: s for s in spans if s.span_id}
    children: dict[str | None, list[TraceSpan]] = {}
    for span in spans:
        parent = span.parent_span_id if span.parent_span_id in by_id else None
        children.setdefault(parent, []).append(span)

    for siblings in children.values():
        siblings.sort(key=lambda s: s.start_time)

    depth_cache: dict[str, int] = {}

    def depth_of(span_id: str) -> int:
        if span_id in depth_cache:
            return depth_cache[span_id]
        span = by_id.get(span_id)
        if span is None:
            return 0
        parent = span.parent_span_id
        if not parent or parent not in by_id:
            depth_cache[span_id] = 0
            return 0
        d = depth_of(parent) + 1
        depth_cache[span_id] = d
        return d

    ref = TelemetryReference(backend="tempo", kind="trace", locator=trace_id)

    envelopes: list[SpanEnvelope] = []
    for _parent_key, siblings in children.items():
        for seq, span in enumerate(siblings):
            parent_id = span.parent_span_id
            if not parent_id:
                # True root: nothing but its own (possibly skewed) start time.
                source = OrderingSource.WALL_CLOCK
            elif parent_id in by_id:
                # Parent present → DAG-ordered, skew-robust.
                source = OrderingSource.TRACE_DAG
            else:
                # Names a parent absent from the fetched trace → degraded.
                source = OrderingSource.FALLBACK
            envelopes.append(
                SpanEnvelope(
                    span=span,
                    trace_id=trace_id,
                    dag_depth=depth_of(span.span_id),
                    logical_seq=seq,
                    ordering_source=source,
                    reference=ref,
                )
            )
    return envelopes
