"""C2 boundary helpers for eval baselines."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from contracts import AnomalyBag, AnomalyOutput, Thresholds


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def bag_from_node_outputs(
    incident_id: str,
    outputs: Iterable[AnomalyOutput],
    thresholds: Thresholds | None = None,
) -> AnomalyBag:
    return AnomalyBag.from_outputs(incident_id, outputs, thresholds)


def bag_from_fixture_nodes(raw: Mapping[str, Any]) -> AnomalyBag:
    """Load golden anomaly.json / anomaly_output.json into AnomalyBag."""
    thr_raw = raw.get("thresholds") or {}
    thresholds = Thresholds(
        theta_fire=float(thr_raw.get("theta_fire", 0.5)),
        k_persist=int(thr_raw.get("k_persist", 3)),
        theta_low=float(thr_raw.get("theta_low", thr_raw.get("θ_low", 0.25))),
        kappa=float(thr_raw.get("kappa", 3.0)),
    )
    nodes: dict[str, AnomalyOutput] = {}
    for nid, n in (raw.get("nodes") or {}).items():
        nodes[nid] = AnomalyOutput(
            node=nid,
            anomaly_score=float(n["anomaly_score"]),
            dev_vector=dict(n.get("dev_vector") or {}),
            first_deviation=_parse_ts(n.get("first_deviation")),
        )
    incident_id = str(raw.get("incident_id") or "unknown")
    return AnomalyBag(incident_id=incident_id, nodes=nodes, thresholds=thresholds)
