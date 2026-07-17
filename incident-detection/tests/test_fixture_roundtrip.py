"""
Golden fixture ↔ contract round-trip (#8): the fixtures load into the C1/C2
types, so B and C build against a shape the contract actually describes.
"""

from __future__ import annotations

import json
from datetime import datetime

from contracts import AnomalyOutput, CallType, IncidentTelemetry


def test_telemetry_round_trips_through_incident_telemetry(golden_dir):
    data = json.loads((golden_dir / "telemetry.json").read_text())
    tel = IncidentTelemetry.from_fixture(data)
    assert tel.incident_id == "golden-0001"
    assert tel.alerted_service == "frontend"
    assert tel.window_start < tel.window_end
    assert len(tel.edges) == len(data["edges"])
    assert all(isinstance(e.call_type, CallType) for e in tel.edges)
    assert {e.call_type for e in tel.edges} == {CallType.SYNC, CallType.ASYNC, CallType.RUNS_ON}


def test_anomaly_nodes_round_trip_and_use_otel_names(golden_dir):
    ao = json.loads((golden_dir / "anomaly_output.json").read_text())
    outs = []
    for node, v in ao["nodes"].items():
        fd = v["first_deviation"]
        outs.append(AnomalyOutput(
            node=node, anomaly_score=v["anomaly_score"], dev_vector=v["dev_vector"],
            first_deviation=datetime.fromisoformat(fd) if fd else None,
        ))
    names = {o.node for o in outs}
    assert "product-catalog" in names                   # otel-demo name, not productcatalogservice
    assert not any("service" in n for n in names)        # no hipstershop chimera
    assert any(o.first_deviation is None for o in outs)  # the slow-burn null survives


def test_fixture_names_join_across_files(golden_dir):
    gt = json.loads((golden_dir / "ground_truth.json").read_text())
    tel = json.loads((golden_dir / "telemetry.json").read_text())
    ao = json.loads((golden_dir / "anomaly_output.json").read_text())
    assert gt["true_root_node"] in tel["services"]
    assert gt["true_root_node"] in ao["nodes"]
