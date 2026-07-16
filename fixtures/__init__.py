"""Load golden fixtures into contract types."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from contracts.c2_anomaly import AnomalyOutput, AnomalyThresholds, NodeAnomaly
from contracts.c3_candidates import RankedResult
from contracts.c5_ground_truth import DualInjectionGroundTruth, Incident

FIXTURE_DIR = Path(__file__).resolve().parent / "golden_incident"


def _parse_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_subgraph(path: Path | None = None) -> dict:
    p = path or (FIXTURE_DIR / "subgraph.json")
    return json.loads(p.read_text())


def load_anomaly(path: Path | None = None) -> AnomalyOutput:
    p = path or (FIXTURE_DIR / "anomaly.json")
    raw = json.loads(p.read_text())
    thr = raw.get("thresholds") or {}
    nodes = {
        nid: NodeAnomaly(
            node_id=nid,
            anomaly_score=float(n["anomaly_score"]),
            dev_vector=dict(n.get("dev_vector") or {}),
            first_deviation=_parse_ts(n.get("first_deviation")),
        )
        for nid, n in (raw.get("nodes") or {}).items()
    }
    return AnomalyOutput(
        incident_id=raw["incident_id"],
        nodes=nodes,
        thresholds=AnomalyThresholds(
            theta_fire=float(thr.get("theta_fire", 0.5)),
            k_persist=int(thr.get("k_persist", 3)),
            theta_low=float(thr.get("theta_low", 0.2)),
        ),
    )


def load_ranked_result(path: Path | None = None) -> RankedResult:
    p = path or (FIXTURE_DIR / "ranked_result.json")
    return RankedResult.from_dict(json.loads(p.read_text()))


def load_ground_truth(path: Path | None = None) -> Incident:
    p = path or (FIXTURE_DIR / "ground_truth.json")
    return Incident.from_dict(json.loads(p.read_text()))


def load_dual_ground_truth(path: Path | None = None) -> DualInjectionGroundTruth:
    p = path or (FIXTURE_DIR / "dual_ground_truth.json")
    return DualInjectionGroundTruth.from_dict(json.loads(p.read_text()))


def load_logs(path: Path | None = None) -> dict[str, list[dict]]:
    p = path or (FIXTURE_DIR / "logs.json")
    return json.loads(p.read_text())
