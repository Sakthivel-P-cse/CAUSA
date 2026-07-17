"""
Ground-truth harness — CRD parsing, S5 node substitution (#4), dry-run
no-persist, collision-free ids, injection detection (#6), and catalog fidelity.
"""

from __future__ import annotations

from datetime import datetime, timezone

from testbed.harness import run_scenario as rs
from testbed.harness.catalog import get


def test_s3_fault_class_is_timing_not_dependency():
    assert get("S3").faults[0].fault_class == "Timing"


def test_d2_shares_a_real_dependency():
    roots = [f.true_root_node for f in get("D2").faults]
    assert roots == ["recommendation", "checkout"]      # both call product-catalog; not the leaf `ad`


def test_crd_parse_extracts_targets_and_node():
    s5 = get("S5")
    docs = rs._crd_documents(s5.crd_path)
    assert docs[0]["kind"] == "StressChaos"
    assert docs[0]["nodes"] == ["kind-worker"]
    assert rs._node_from_crd(s5.crd_path) == "kind-worker"


def test_s5_node_sentinel_is_substituted():
    now = datetime.now(timezone.utc)
    # from the CRD selector
    incs = rs._build_incidents(get("S5"), now, now, None)
    assert incs[0].true_root_node == "kind-worker"
    # explicit --node overrides
    incs2 = rs._build_incidents(get("S5"), now, now, "node-7")
    assert incs2[0].true_root_node == "node-7"


def test_incident_ids_do_not_collide():
    now = datetime.now(timezone.utc)
    a = rs._build_incidents(get("S1"), now, now, None)[0].incident_id
    b = rs._build_incidents(get("S1"), now, now, None)[0].incident_id
    assert a != b


def test_dry_run_returns_without_persisting(tmp_path):
    incs = rs.run_scenario(get("S1"), apply=False, out_dir=tmp_path, hold_seconds=1)
    assert incs                                          # built for inspection
    assert list(tmp_path.iterdir()) == []               # but nothing persisted


def test_injection_detection():
    assert rs._is_injected('{"status":{"conditions":[{"type":"AllInjected","status":"True"}]}}')
    assert not rs._is_injected('{"status":{"conditions":[{"type":"Selected","status":"True"}]}}')
    assert not rs._is_injected("not json")
