from __future__ import annotations

from contracts import CallType, Edge, InfraNode, ServiceEdge, SpanKind, TraceSpan


def classify_call(span: TraceSpan) -> CallType:
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
    by_parent: dict[str, list[TraceSpan]] = {}
    for s in spans:
        if s.parent_span_id:
            by_parent.setdefault(s.parent_span_id, []).append(s)

    out: dict[tuple[str, str], CallType] = {}
    for span in spans:
        if span.kind not in (SpanKind.CLIENT, SpanKind.PRODUCER):
            continue
        caller = span.service
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
            if out.get(key) != CallType.ASYNC:
                out[key] = ct
    return out


def service_edges_from_spans(spans: list[TraceSpan]) -> list[ServiceEdge]:
    ct_map = call_types_from_spans(spans)
    edges: list[ServiceEdge] = []
    seen: set[tuple[str, str]] = set()
    for (caller, callee), ct in ct_map.items():
        if not caller or not callee or caller == callee:
            continue
        key = (caller, callee)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            ServiceEdge(
                caller=caller,
                callee=callee,
                request_rate=0.0,
                error_rate=0.0,
                latency_p99_ms=0.0,
                call_type=ct,
            )
        )
    return edges


def merge_service_edges(
    span_edges: list[ServiceEdge],
    graph_edges: list[ServiceEdge],
) -> list[ServiceEdge]:
    by_key: dict[tuple[str, str], ServiceEdge] = {}
    for e in span_edges:
        by_key[(e.caller, e.callee)] = e
    for g in graph_edges:
        key = (g.caller, g.callee)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = g
            continue
        by_key[key] = ServiceEdge(
            caller=existing.caller,
            callee=existing.callee,
            request_rate=g.request_rate or existing.request_rate,
            error_rate=g.error_rate if g.error_rate else existing.error_rate,
            latency_p99_ms=g.latency_p99_ms or existing.latency_p99_ms,
            call_type=existing.call_type if existing.call_type != CallType.SYNC else g.call_type,
        )
    return list(by_key.values())


def _filter_edges_by_allowlist(
    edges: list[ServiceEdge],
    allowlist: set[str] | None,
) -> list[ServiceEdge]:
    if not allowlist:
        return edges
    allowed = {s for s in allowlist if s}
    if not allowed:
        return edges
    out: list[ServiceEdge] = []
    for e in edges:
        if e.caller in allowed and e.callee in allowed:
            out.append(e)
        elif e.caller in allowed or e.callee in allowed:
            out.append(e)
    return out


def resolve_service_edges(
    spans: list[TraceSpan],
    graph_edges: list[ServiceEdge],
    *,
    service_allowlist: set[str] | None = None,
) -> list[ServiceEdge]:
    span_edges = service_edges_from_spans(spans)
    merged = merge_service_edges(span_edges, graph_edges) if span_edges else list(graph_edges)
    if not merged and graph_edges:
        merged = list(graph_edges)
    return _filter_edges_by_allowlist(merged, service_allowlist)


def build_unified_topology(
    service_edges: list[ServiceEdge],
    infra_nodes: list[InfraNode],
) -> list[Edge]:
    edges: list[Edge] = [
        Edge(src=e.caller, dst=e.callee, call_type=e.call_type) for e in service_edges
    ]
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
