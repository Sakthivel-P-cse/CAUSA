"""
topology.py — derive edge `call_type` and emit the unified topology.

This is the input Dev B's Influence Graph is blocked on (RCA_Engine_Spec §1.2).
It produces ONE list of edges that unifies:
  • service call edges, each tagged sync|async (from span kind + attributes), and
  • infra `runs-on` edges (node → service, from kube_pod_info).

Dev B re-orients this: sync edges reverse (callee→caller), async edges keep
(producer→consumer), runs-on orients infra→service — dissolving the D-at-leaves
bug structurally.
"""

from __future__ import annotations

from contracts import CallType, Edge, InfraNode, ServiceEdge, SpanKind, TraceSpan


def classify_call(span: TraceSpan) -> CallType:
    """
    Classify one CLIENT/PRODUCER span's edge as sync or async. Precedence:
      1. messaging.system present, OR kind PRODUCER/CONSUMER  → async
      2. rpc.system / db.system / http.* / net.peer.* present,
         OR kind CLIENT/SERVER                                → sync
      3. otherwise                                            → sync (safe default)
    """
    attrs = span.attributes or {}
    if "messaging.system" in attrs or span.kind in (SpanKind.PRODUCER, SpanKind.CONSUMER):
        return CallType.ASYNC
    if (
        "rpc.system" in attrs
        or "db.system" in attrs
        or any(k.startswith("http.") for k in attrs)
        or any(k.startswith("net.peer.") for k in attrs)
        or span.kind in (SpanKind.CLIENT, SpanKind.SERVER)
    ):
        return CallType.SYNC
    return CallType.SYNC


def call_types_from_spans(spans: list[TraceSpan]) -> dict[tuple[str, str], CallType]:
    """
    Build a {(caller_service, callee_service): CallType} map from a trace.

    The callee of an outbound (CLIENT/PRODUCER) span is resolved from its child
    span's service (parent→child linkage), falling back to a peer attribute. If
    a pair shows any async evidence it is recorded async (async dominates: a
    Kafka hop between two services is async even if they also call each other
    synchronously elsewhere).
    """
    by_parent: dict[str, list[TraceSpan]] = {}
    for s in spans:
        if s.parent_span_id:
            by_parent.setdefault(s.parent_span_id, []).append(s)

    out: dict[tuple[str, str], CallType] = {}
    for span in spans:
        if span.kind not in (SpanKind.CLIENT, SpanKind.PRODUCER):
            continue
        caller = span.service
        # Callee: child span's service, else a peer attribute.
        callees: list[str] = [
            c.service for c in by_parent.get(span.span_id, []) if c.service and c.service != caller
        ]
        if not callees:
            peer = (
                span.attributes.get("net.peer.name")
                or span.attributes.get("server.address")
                or span.attributes.get("peer.service")
            )
            if peer:
                callees = [peer]
        ct = classify_call(span)
        for callee in callees:
            key = (caller, callee)
            # async dominates once observed
            if out.get(key) != CallType.ASYNC:
                out[key] = ct
    return out


def stamp_service_edges(
    edges: list[ServiceEdge], spans: list[TraceSpan]
) -> list[ServiceEdge]:
    """Set each ServiceEdge.call_type from span-derived evidence (default sync)."""
    ct_map = call_types_from_spans(spans)
    for e in edges:
        e.call_type = ct_map.get((e.caller, e.callee), e.call_type)
    return edges


def build_topology(
    service_edges: list[ServiceEdge],
    infra_nodes: list[InfraNode],
) -> list[Edge]:
    """
    Emit the unified [Edge] topology: service call edges (with their call_type)
    plus infra runs-on edges (host node → service).
    """
    edges: list[Edge] = [
        Edge(src=e.caller, dst=e.callee, call_type=e.call_type) for e in service_edges
    ]

    # runs-on: the pod's host node → the service it hosts. Dedup on (node, service).
    seen: set[tuple[str, str]] = set()
    for n in infra_nodes:
        if n.kind == "pod" and n.host_node and n.service_name:
            key = (n.host_node, n.service_name)
            if key not in seen:
                seen.add(key)
                edges.append(
                    Edge(src=n.host_node, dst=n.service_name, call_type=CallType.RUNS_ON)
                )
    return edges
