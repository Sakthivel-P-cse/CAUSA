"""Offline fixtures for contract, RCA kernel, and Dev C eval tests."""

from __future__ import annotations

import json
from pathlib import Path

from adapters.anomaly import bag_from_fixture_nodes
from adapters.logs import messages_from_fixture_logs
from contracts import AnomalyBag, DualInjectionGroundTruth, Incident, RankedResult

FIXTURE_DIR = Path(__file__).resolve().parent / "golden_incident"
GOLDEN_INCIDENT_ID = "golden-0001"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def load_telemetry(path: Path | None = None) -> dict:
    p = path or (FIXTURE_DIR / "telemetry.json")
    return _load_json(p)


def load_subgraph(path: Path | None = None) -> dict:
    """Eval-facing subgraph dict derived from A+B golden telemetry."""
    if path is not None and path.exists():
        return _load_json(path)
    explicit = FIXTURE_DIR / "subgraph.json"
    if explicit.exists():
        return _load_json(explicit)
    topo = load_telemetry()
    services = list(topo.get("services") or [])
    node_ids = set(services)
    for e in topo.get("edges") or []:
        node_ids.add(e["src"])
        node_ids.add(e["dst"])
    return {
        "incident_id": topo.get("incident_id", GOLDEN_INCIDENT_ID),
        "nodes": [{"id": nid, "kind": "service"} for nid in sorted(node_ids)],
        "edges": list(topo.get("edges") or []),
        "true_root": "product-catalog",
        "description": "A+B golden S0 product-catalog cascade",
    }


def load_anomaly(path: Path | None = None) -> AnomalyBag:
    if path is not None:
        raw = _load_json(path)
    else:
        p = FIXTURE_DIR / "anomaly_output.json"
        if not p.exists():
            p = FIXTURE_DIR / "anomaly.json"
        raw = _load_json(p)
    bag = bag_from_fixture_nodes(raw)
    if bag.incident_id == "unknown":
        bag.incident_id = GOLDEN_INCIDENT_ID
    return bag


def load_ranked_result(path: Path | None = None) -> RankedResult:
    if path is not None:
        return RankedResult.from_dict(_load_json(path))
    flat = FIXTURE_DIR / "root_cause_candidate.json"
    clustered = FIXTURE_DIR / "ranked_result.json"
    if flat.exists():
        return RankedResult.from_dict(_load_json(flat))
    return RankedResult.from_dict(_load_json(clustered))


def load_ground_truth(path: Path | None = None) -> Incident:
    p = path or (FIXTURE_DIR / "ground_truth.json")
    raw = _load_json(p)
    if "subgraph_node_ids" not in raw:
        sub = load_subgraph()
        raw = {
            **raw,
            "subgraph_node_ids": [n["id"] for n in sub.get("nodes", [])],
            "scenario": raw.get("scenario") or "S0",
            "injection_site": raw.get("injection_site") or raw.get("true_root_node"),
        }
    return Incident.from_dict(raw)


def load_dual_ground_truth(path: Path | None = None) -> DualInjectionGroundTruth:
    p = path or (FIXTURE_DIR / "dual_ground_truth.json")
    return DualInjectionGroundTruth.from_dict(_load_json(p))


def load_logs(path: Path | None = None) -> dict[str, list[dict]]:
    p = path or (FIXTURE_DIR / "logs.json")
    return _load_json(p)


def load_logs_by_node(path: Path | None = None) -> dict[str, list[str]]:
    return messages_from_fixture_logs(load_logs(path))
