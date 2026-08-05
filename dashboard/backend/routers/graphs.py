from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from dashboard.backend.dependencies import get_prometheus, get_store, get_tempo
from dashboard.backend.db import DashboardStore
from dashboard.backend.errors import api_error, raise_upstream_error

router = APIRouter()

SYSTEM_NAMESPACES = {
    "alertmanager",
    "causa-dashboard",
    "causa-ingest",
    "causa-remediation",
    "kube-node-lease",
    "kube-public",
    "kube-state-metrics",
    "kube-system",
    "local-path-storage",
    "loki",
    "mimir",
    "minio-loki",
    "minio-mimir",
    "minio-tempo",
    "node-exporter",
    "otel-agent",
    "otel-collector",
    "otel-lb",
    "postgres",
    "prometheus",
    "stage2-rca",
    "tempo",
}


def _infra_health(
    *,
    kind: str,
    namespace: str | None,
    condition: str | None,
    restart_count: int,
    cpu: float,
    memory: float,
    service_name: str | None,
    active_services: set[str] | None,
) -> tuple[str, str, str]:
    if condition and condition not in {"Ready", "Running"}:
        return "error", "disconnected", f"Condition: {condition}"
    if restart_count >= 3:
        return "error", "fault", f"{restart_count} container restarts"
    if cpu > 90 or memory > 90:
        resource = "CPU" if cpu > 90 else "memory"
        return "error", "fault", f"{resource} usage above 90%"
    if kind != "pod" or namespace in SYSTEM_NAMESPACES:
        return "infra", "infrastructure", "Platform telemetry only"
    if restart_count > 0:
        return "warn", "degraded", f"{restart_count} container restart"
    if cpu > 70 or memory > 80:
        resource = "CPU" if cpu > 70 else "memory"
        return "warn", "degraded", f"High {resource} usage"
    if active_services is None:
        return "unknown", "unknown", "Telemetry check unavailable"
    if service_name not in active_services:
        return "error", "no_telemetry", "No active application telemetry"
    return "ok", "telemetry_active", "Application telemetry active"


def _edge_to_dict(e: Any) -> dict[str, Any]:
    return {
        "source": getattr(e, "caller", None) or getattr(e, "source", None),
        "target": getattr(e, "callee", None) or getattr(e, "target", None),
        "request_rate": float(getattr(e, "request_rate", 0) or 0),
        "error_rate": float(getattr(e, "error_rate", 0) or 0),
        "latency_p99_ms": float(getattr(e, "latency_p99_ms", 0) or 0),
    }


@router.get("/service-dependency")
def service_dependency(
    service: str | None = Query(None),
    minutes: int = Query(5, ge=1, le=1440),
    prom=Depends(get_prometheus),
) -> dict[str, Any]:
    try:
        prom.check_available()
        edges = [_edge_to_dict(e) for e in prom.get_service_edges(window=f"{minutes}m")]
        if service:
            edges = [
                edge for edge in edges
                if service in {edge.get("source"), edge.get("target")}
            ]
    except Exception as exc:
        raise_upstream_error("prometheus", "service dependency", exc)
    nodes: dict[str, dict[str, Any]] = {}
    for e in edges:
        for key in ("source", "target"):
            name = e.get(key)
            if name and name not in nodes:
                nodes[name] = {"id": name, "label": name, "kind": "service", "status": "ok"}
        if e.get("error_rate", 0) > 0.05 and e.get("target") in nodes:
            nodes[e["target"]]["status"] = "error"
    return {
        "nodes": list(nodes.values()),
        "edges": [
            {
                "id": f"{e['source']}->{e['target']}",
                "source": e["source"],
                "target": e["target"],
                "label": f"{e['request_rate']:.1f} r/s",
                "error_rate": e["error_rate"],
                "animated": e["error_rate"] >= 0.05,
            }
            for e in edges
            if e.get("source") and e.get("target")
        ],
    }


@router.get("/infrastructure")
def infrastructure(
    service: str | None = Query(None),
    prom=Depends(get_prometheus),
) -> dict[str, Any]:
    try:
        infra = prom.get_infra_nodes()
    except Exception as exc:
        raise_upstream_error("prometheus", "infrastructure", exc)
    try:
        active_services: set[str] | None = prom.get_active_service_names()
    except Exception:
        active_services = None
    nodes = []
    for n in infra:
        name = getattr(n, "name", None) or getattr(n, "node", None) or str(n)
        if service and service.lower() not in name.lower():
            continue
        kind = getattr(n, "kind", None) or "node"
        cpu = float(getattr(n, "cpu_usage_pct", 0) or 0)
        memory = float(getattr(n, "memory_usage_pct", 0) or 0)
        namespace = getattr(n, "namespace", None)
        condition = getattr(n, "condition", None)
        restart_count = int(getattr(n, "restart_count", 0) or 0)
        service_name = getattr(n, "service_name", None)
        status, health_reason, status_detail = _infra_health(
            kind=kind,
            namespace=namespace,
            condition=condition,
            restart_count=restart_count,
            cpu=cpu,
            memory=memory,
            service_name=service_name,
            active_services=active_services,
        )
        nodes.append(
            {
                "id": name,
                "label": name,
                "kind": kind,
                "status": status,
                "health_reason": health_reason,
                "status_detail": status_detail,
                "cpu_pct": cpu,
                "memory_pct": memory,
                "condition": condition,
                "restart_count": restart_count,
                "namespace": namespace,
                "service_name": service_name,
                "host_node": getattr(n, "host_node", None),
            }
        )
    return {
        "nodes": nodes,
        "edges": [],
        "status_legend": [
            {"status": "error", "label": "Fault / disconnected / no telemetry"},
            {"status": "warn", "label": "Degraded"},
            {"status": "ok", "label": "Telemetry active"},
            {"status": "infra", "label": "Platform infrastructure"},
            {"status": "unknown", "label": "Telemetry unknown"},
        ],
    }


@router.get("/trace-execution")
def trace_execution(
    trace_id: str = Query(...),
    tempo=Depends(get_tempo),
) -> dict[str, Any]:
    try:
        spans = tempo.get_trace(trace_id)
    except Exception as exc:
        raise_upstream_error("tempo", "trace graph", exc, not_found=True)
    nodes = []
    edges = []
    for s in spans:
        nodes.append(
            {
                "id": s.span_id,
                "label": f"{s.service}:{s.operation}",
                "kind": "span",
                "status": "error" if s.status_code == 2 else "ok",
                "service": s.service,
                "duration_ms": s.duration_ms,
            }
        )
        if s.parent_span_id:
            edges.append(
                {
                    "id": f"{s.parent_span_id}->{s.span_id}",
                    "source": s.parent_span_id,
                    "target": s.span_id,
                    "animated": s.status_code == 2,
                }
            )
    return {"nodes": nodes, "edges": edges}


@router.get("/incident")
def incident_graph(
    report_id: str = Query(...),
    store: DashboardStore = Depends(get_store),
) -> dict[str, Any]:
    item = store.get_report(report_id)
    if not item:
        raise api_error(404, "not_found", "Report not found")
    nodes: dict[str, dict[str, Any]] = {}
    edges = []
    origin = item.get("root_cause_service")
    for cand in item.get("origin_candidates") or []:
        nid = cand.get("node_id")
        if not nid:
            continue
        role = cand.get("role") or "candidate"
        status = "origin" if role == "origin" else "amplifier" if role == "amplifier" else "ok"
        nodes[nid] = {
            "id": nid,
            "label": nid,
            "kind": cand.get("node_kind") or "service",
            "status": status,
            "role": role,
            "score": cand.get("score"),
        }
        if origin and nid != origin:
            edges.append(
                {
                    "id": f"{origin}->{nid}",
                    "source": origin,
                    "target": nid,
                    "animated": role == "amplifier",
                }
            )
    for nid in item.get("blast_radius") or []:
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": nid, "kind": "service", "status": "blast"}
        if origin and nid != origin:
            edges.append(
                {
                    "id": f"{origin}-blast-{nid}",
                    "source": origin,
                    "target": nid,
                    "style": "dashed",
                }
            )
    if origin and origin not in nodes:
        nodes[origin] = {
            "id": origin,
            "label": origin,
            "kind": "service",
            "status": "origin",
            "role": "origin",
        }
    return {"nodes": list(nodes.values()), "edges": edges, "origin": origin}
