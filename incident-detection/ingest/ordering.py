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
    Attach causal DAG depth + sibling logical_seq to each span, record which tier
    of the causality hierarchy fixed its order, and return the envelopes in
    TOPOLOGICAL order — every parent precedes its children.

    A trace is a tree (one parent per span), so ordering by ascending `dag_depth`
    is a valid topological sort; ties are broken by `logical_seq` (within-parent
    wall-time) then `span_id` for determinism. This makes the adapter's
    "DAG-ordered" promise true positionally, not just via the `dag_depth` field
    a consumer would otherwise have to re-sort on (audit M6).

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

    def depth_of(start_id: str) -> int:
        """Depth via an ITERATIVE walk up the parent chain — cycle- and
        recursion-safe: a self/ancestor-referential span (malformed data) or a
        very deep chain degrades gracefully instead of hitting RecursionError
        (audit L3)."""
        chain: list[str] = []
        seen: set[str] = set()
        cur = start_id
        top_depth = 0
        while True:
            if cur in depth_cache:
                top_depth = depth_cache[cur] + 1   # node below a known one is +1
                break
            span = by_id.get(cur)
            if span is None or cur in seen:
                top_depth = 0                       # dangling/cycle → treat as root
                break
            seen.add(cur)
            parent = span.parent_span_id
            chain.append(cur)
            if not parent or parent not in by_id:
                top_depth = 0                       # genuine root
                break
            cur = parent
        for i, sid in enumerate(reversed(chain)):   # top of chain gets top_depth
            depth_cache[sid] = top_depth + i
        return depth_cache.get(start_id, 0)

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

    # Topological order: parent (lower depth) before child (higher depth).
    envelopes.sort(
        key=lambda e: (
            e.dag_depth if e.dag_depth is not None else 0,
            e.logical_seq if e.logical_seq is not None else 0,
            e.span.span_id,
        )
    )
    return envelopes
