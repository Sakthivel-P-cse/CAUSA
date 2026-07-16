"""
Golden fixture self-consistency — the week-0 artifact B and C build against.

Guards the invariants the other lanes rely on: the fixture parses into the
contract types, the ground-truth root is a real node, edges carry valid
call_types, and at least one slow-burn node has first_deviation == null.
"""

from __future__ import annotations

import json

from contracts import CallType, FaultClass, Incident


def _load(golden_dir, name):
    return json.loads((golden_dir / name).read_text())


def test_ground_truth_builds_incident(golden_dir):
    gt = _load(golden_dir, "ground_truth.json")
    inc = Incident(
        incident_id=gt["incident_id"],
        true_root_node=gt["true_root_node"],
        fault_class=gt["fault_class"],
        inject_start=gt["inject_start"],
        inject_end=gt["inject_end"],
    )
    assert inc.fault_class in {c.value for c in FaultClass}


def test_ground_truth_root_is_a_known_service(golden_dir):
    gt = _load(golden_dir, "ground_truth.json")
    topo = _load(golden_dir, "telemetry.json")
    assert gt["true_root_node"] in topo["services"]


def test_edges_have_valid_call_types(golden_dir):
    topo = _load(golden_dir, "telemetry.json")
    valid = {c.value for c in CallType}
    assert topo["edges"]
    for e in topo["edges"]:
        assert e["call_type"] in valid


def test_has_a_slow_burn_null(golden_dir):
    ao = _load(golden_dir, "anomaly_output.json")
    nulls = [n for n, v in ao["nodes"].items() if v["first_deviation"] is None]
    assert nulls, "golden incident must contain at least one slow-burn (null) node"


def test_root_cause_ranked_first(golden_dir):
    rcc = _load(golden_dir, "root_cause_candidate.json")
    gt = _load(golden_dir, "ground_truth.json")
    top = rcc["candidates"][0]
    assert top["node_id"] == gt["true_root_node"]
    assert top["role"] == "origin"
