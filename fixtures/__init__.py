"""Load golden fixtures into contract types via adapters."""

from __future__ import annotations

import json
from pathlib import Path

from adapters.anomaly import bag_from_fixture_nodes
from adapters.logs import messages_from_fixture_logs
from contracts import AnomalyBag, DualInjectionGroundTruth, Incident, RankedResult

FIXTURE_DIR = Path(__file__).resolve().parent / "golden_incident"
GOLDEN_INCIDENT_ID = "golden-001"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def load_subgraph(path: Path | None = None) -> dict:
    p = path or (FIXTURE_DIR / "subgraph.json")
    return _load_json(p)


def load_anomaly(path: Path | None = None) -> AnomalyBag:
    p = path or (FIXTURE_DIR / "anomaly.json")
    if not p.exists():
        alt = FIXTURE_DIR / "anomaly_output.json"
        p = alt if alt.exists() else p
    return bag_from_fixture_nodes(_load_json(p))


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
    return Incident.from_dict(_load_json(p))


def load_dual_ground_truth(path: Path | None = None) -> DualInjectionGroundTruth:
    p = path or (FIXTURE_DIR / "dual_ground_truth.json")
    return DualInjectionGroundTruth.from_dict(_load_json(p))


def load_logs(path: Path | None = None) -> dict[str, list[dict]]:
    p = path or (FIXTURE_DIR / "logs.json")
    return _load_json(p)


def load_logs_by_node(path: Path | None = None) -> dict[str, list[str]]:
    return messages_from_fixture_logs(load_logs(path))
