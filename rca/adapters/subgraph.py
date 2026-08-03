"""Build IncidentSubgraph + KernelAnomaly from A+B golden fixtures."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from adapters.anomaly import bag_from_fixture_nodes
from contracts import CallType, NodeKind
from graphs.incident_subgraph import IncidentSubgraph
from graphs.types import Edge, Node
from rca.types import KernelAnomaly

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "eval" / "fixtures" / "golden_incident"
GOLDEN_INCIDENT_ID = "golden-0001"


def _load_json(name: str) -> dict:
    return json.loads((GOLDEN_DIR / name).read_text())


def _parse_ts(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.timestamp()


def _kind_for(nid: str, runs_on_sources: set[str]) -> NodeKind:
    if nid in runs_on_sources or nid.startswith("node-"):
        return NodeKind.INFRA
    if "valkey" in nid or "redis" in nid or nid.endswith("-db"):
        return NodeKind.DEPENDENCY
    return NodeKind.SERVICE


def kernel_anomaly_from_golden() -> KernelAnomaly:
    raw = _load_json("anomaly_output.json")
    bag = bag_from_fixture_nodes(raw)
    scores: dict[str, float] = {}
    first: dict[str, float | None] = {}
    resource: dict[str, bool] = {}
    dev: dict[str, dict[str, float]] = {}
    t0_candidates: list[float] = []
    for nid, out in bag.nodes.items():
        scores[nid] = out.anomaly_score
        ts = _parse_ts(out.first_deviation)
        first[nid] = ts
        if ts is not None:
            t0_candidates.append(ts)
        resource[nid] = (
            out.dev_vector.get("saturation", 0.0) >= 0.8
            or out.dev_vector.get("cpu", 0.0) >= 0.8
        )
        dev[nid] = dict(out.dev_vector)
    t0 = min(t0_candidates) if t0_candidates else 0.0
    first_rel = {
        nid: (None if v is None else v - t0) for nid, v in first.items()
    }
    return KernelAnomaly(
        anomaly_score=scores,
        first_deviation_time=first_rel,
        resource_deviation=resource,
        anomalous_trace_ids=frozenset(),
        t0=0.0,
        dev_vector=dev,
        thresholds=bag.thresholds,
    )


def incident_subgraph_from_golden(
    subgraph_dict: Mapping[str, Any] | None = None,
) -> IncidentSubgraph:
    """Materialize IncidentSubgraph for C4 scoring from golden telemetry+anomaly."""
    topo = _load_json("telemetry.json")
    anomaly = kernel_anomaly_from_golden()
    runs_on_sources = {
        e["src"] for e in topo.get("edges") or [] if e.get("call_type") == "runs-on"
    }
    node_ids: set[str] = set(topo.get("services") or [])
    for e in topo.get("edges") or []:
        node_ids.add(e["src"])
        node_ids.add(e["dst"])
    if subgraph_dict:
        for n in subgraph_dict.get("nodes") or []:
            node_ids.add(n["id"] if isinstance(n, dict) else str(n))

    nodes = {
        nid: Node(nid, _kind_for(nid, runs_on_sources), nid)
        for nid in node_ids
    }
    edges: list[Edge] = []
    for e in topo.get("edges") or []:
        src, dst = e["src"], e["dst"]
        if src not in nodes or dst not in nodes:
            continue
        edges.append(
            Edge(
                src=src,
                dst=dst,
                call_type=CallType(e["call_type"]),
                degraded=anomaly.anomaly_score.get(dst, 0.0) >= 0.5
                or anomaly.anomaly_score.get(src, 0.0) >= 0.5,
            )
        )

    seeds = {
        nid
        for nid, s in anomaly.anomaly_score.items()
        if s > anomaly.thresholds.theta_low
    }
    return IncidentSubgraph(
        nodes=nodes,
        edges=edges,
        anomaly=anomaly,
        theta_fire=anomaly.thresholds.theta_fire,
        theta_low=anomaly.thresholds.theta_low,
        seed_ids=seeds,
    )


def resolve_incident_id(subgraph: Any) -> str:
    if isinstance(subgraph, dict):
        return str(subgraph.get("incident_id") or GOLDEN_INCIDENT_ID)
    return GOLDEN_INCIDENT_ID
