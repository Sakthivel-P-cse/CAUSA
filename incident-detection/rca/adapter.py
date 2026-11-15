"""Adapt canonical Dev A C1/C2 values to Dev B's internal kernel model."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from contracts import (
    AnomalyOutput,
    CallType,
    IncidentTelemetry,
    NodeKind,
    Thresholds,
)
from graphs.types import Edge, Node, Span, Topology, Trace

from .types import KernelAnomaly

_RESOURCE_METRICS = frozenset({"cpu", "saturation"})


@dataclass(frozen=True)
class RCAInput:
    topology: Topology
    traces: tuple[Trace, ...]
    anomaly: KernelAnomaly


def _offset_seconds(value: datetime | None, window_start: datetime) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("Anomaly first_deviation must be timezone-aware")
    return (value - window_start).total_seconds()


def adapt_incident(
    telemetry: IncidentTelemetry,
    anomalies: Iterable[AnomalyOutput],
    *,
    thresholds: Thresholds | None = None,
) -> RCAInput:
    """Convert canonical telemetry and per-node anomalies into kernel input."""
    thresholds = thresholds or Thresholds()
    if telemetry.window_start.tzinfo is None or telemetry.window_end.tzinfo is None:
        raise ValueError("Incident telemetry window must be timezone-aware")

    by_node = {output.node: output for output in anomalies}
    anomaly_scores = {node: output.anomaly_score for node, output in by_node.items()}
    first_deviations = {
        node: _offset_seconds(output.first_deviation, telemetry.window_start)
        for node, output in by_node.items()
    }
    resource_deviations = {
        node: any(
            output.dev_vector.get(metric, 0.0) >= thresholds.theta_fire
            for metric in _RESOURCE_METRICS
        )
        for node, output in by_node.items()
    }

    service_names = {telemetry.alerted_service}
    infra_names: set[str] = set()
    runs_on: dict[str, str] = {}
    # Positive evidence for classification. A node is DEPENDENCY only when it is
    # demonstrably CALLED (in `callees`) yet neither emits spans of its own nor
    # calls anyone — being absent from emitters/callers is not itself evidence,
    # since a node known only from an anomaly row has no call-graph presence at
    # all and must not be demoted on that silence.
    emitters: set[str] = set()
    callers: set[str] = set()
    callees: set[str] = set()

    for infra in telemetry.infra_nodes:
        # Only HOSTS are infra. `get_infra_nodes` also returns pod rows, and
        # adding those names made every pod in the cluster an isolated INFRA
        # vertex — a pod is where a workload runs, not a layer of its own.
        if infra.kind == "node":
            infra_names.add(infra.name)
        if infra.host_node:
            infra_names.add(infra.host_node)
        if infra.service_name and infra.host_node:
            service_names.add(infra.service_name)
            runs_on[infra.service_name] = infra.host_node

    for envelope in telemetry.spans:
        service_names.add(envelope.span.service)
        emitters.add(envelope.span.service)
    for edge in telemetry.service_edges:
        service_names.update((edge.caller, edge.callee))
        callers.add(edge.caller)
        callees.add(edge.callee)
    for edge in telemetry.edges:
        if edge.call_type is CallType.RUNS_ON:
            infra_names.add(edge.src)
            service_names.add(edge.dst)
            runs_on[edge.dst] = edge.src
        else:
            service_names.update((edge.src, edge.dst))
            callers.add(edge.src)
            callees.add(edge.dst)

    def _classify(name: str) -> NodeKind:
        if name in infra_names:
            return NodeKind.INFRA
        # DEPENDENCY requires positive evidence: something calls it, and it
        # neither emits spans nor calls anything itself. Everything else —
        # including a node with no call-graph presence at all — stays SERVICE
        # rather than being demoted on absent evidence.
        if name in callees and name not in emitters and name not in callers:
            return NodeKind.DEPENDENCY
        return NodeKind.SERVICE

    all_names = service_names | infra_names | set(by_node)
    nodes = tuple(
        Node(
            node_id=name,
            kind=_classify(name),
            name=name,
            runs_on=runs_on.get(name),
        )
        for name in sorted(all_names)
    )

    canonical_edges = [
        (edge.src, edge.dst, edge.call_type) for edge in telemetry.edges
    ]
    if not canonical_edges:
        canonical_edges = [
            (edge.caller, edge.callee, edge.call_type)
            for edge in telemetry.service_edges
        ]

    edges: list[Edge] = []
    seen_edges: set[tuple[str, str, CallType]] = set()
    for src, dst, call_type in canonical_edges:
        key = (src, dst, call_type)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        degraded = max(
            anomaly_scores.get(src, 0.0),
            anomaly_scores.get(dst, 0.0),
        ) >= thresholds.theta_fire
        edges.append(Edge(src, dst, call_type, degraded=degraded))

    spans_by_trace: dict[str, list[Span]] = defaultdict(list)
    trace_services: dict[str, set[str]] = defaultdict(set)
    trace_has_error: dict[str, bool] = defaultdict(bool)
    for envelope in telemetry.spans:
        source = envelope.span
        start_ms = (source.start_time - telemetry.window_start).total_seconds() * 1000
        spans_by_trace[envelope.trace_id].append(
            Span(
                span_id=source.span_id,
                service_id=source.service,
                parent_span_id=source.parent_span_id,
                start_ms=start_ms,
                end_ms=start_ms + source.duration_ms,
                span_kind=source.kind.value.upper(),
                attributes=dict(source.attributes),
            )
        )
        trace_services[envelope.trace_id].add(source.service)
        trace_has_error[envelope.trace_id] |= source.status_code == 2

    anomalous_trace_ids = {
        trace_id
        for trace_id, services in trace_services.items()
        if trace_has_error[trace_id]
        or any(anomaly_scores.get(service, 0.0) >= thresholds.theta_fire for service in services)
    }
    if telemetry.trace_id and any(
        score >= thresholds.theta_fire for score in anomaly_scores.values()
    ):
        anomalous_trace_ids.add(telemetry.trace_id)

    traces = tuple(
        Trace(
            trace_id=trace_id,
            spans=tuple(sorted(spans, key=lambda span: (span.start_ms, span.span_id))),
            anomalous=trace_id in anomalous_trace_ids,
        )
        for trace_id, spans in sorted(spans_by_trace.items())
    )
    anomaly = KernelAnomaly(
        anomaly_score=anomaly_scores,
        first_deviation_time=first_deviations,
        resource_deviation=resource_deviations,
        anomalous_trace_ids=frozenset(anomalous_trace_ids),
        dev_vector={node: dict(output.dev_vector) for node, output in by_node.items()},
        thresholds=thresholds,
    )
    return RCAInput(topology=Topology(nodes=nodes, edges=tuple(edges)), traces=traces, anomaly=anomaly)
