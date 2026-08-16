"""
Golden fixture ↔ contract round-trip (#8): the fixtures load into the C1/C2
types, so B and C build against a shape the contract actually describes.
"""

from __future__ import annotations

import json
from datetime import datetime

from anomaly import load_baselines
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


def _spec_T(fdt_by_node: dict[str, datetime | None]) -> dict[str, float]:
    """The C3 T signal per spec §2.1, from each node's FirstDeviationTime."""
    present = [t for t in fdt_by_node.values() if t is not None]
    t0 = min(present)
    horizon = min(max((t - t0).total_seconds() for t in present), 60.0)  # H = 60s
    out: dict[str, float] = {}
    for node, t in fdt_by_node.items():
        if t is None:
            out[node] = 0.5                                  # slow-burn → neutral
        else:
            delta = (t - t0).total_seconds()
            out[node] = max(0.0, min(1.0, 1.0 - delta / (horizon + 1e-9)))
    return out


def test_candidate_T_signals_match_spec_2_1(golden_dir):
    # Audit M10: the C3 T signals must be derivable from anomaly_output's
    # first_deviation via the spec §2.1 time-delta formula — no free-floating T.
    ao = json.loads((golden_dir / "anomaly_output.json").read_text())["nodes"]
    cand = json.loads((golden_dir / "root_cause_candidate.json").read_text())["candidates"]

    def _fdt(node: str):
        fd = ao[node]["first_deviation"]
        return datetime.fromisoformat(fd) if fd else None

    fdt_by_node = {c["node_id"]: _fdt(c["node_id"]) for c in cand}
    expected = _spec_T(fdt_by_node)
    for c in cand:
        assert abs(c["signals"]["T"] - expected[c["node_id"]]) < 0.02, (
            f"{c['node_id']}: T={c['signals']['T']} but §2.1 gives {expected[c['node_id']]}"
        )


def test_baseline_json_loads_and_covers_every_dev_vector_metric(golden_dir):
    # Audit L2: ship a baseline so all lanes score against identical median/MAD.
    base = load_baselines(golden_dir / "baseline.json")
    assert base and all(b.mad > 0 and b.n > 0 for b in base.values())  # finite, usable

    ao = json.loads((golden_dir / "anomaly_output.json").read_text())["nodes"]
    for node, v in ao.items():
        for metric in v["dev_vector"]:
            assert (node, metric) in base, f"baseline.json missing ({node}, {metric})"
