"""
Chaos catalog integrity — every scenario maps to a real CRD file with a valid
fault class, and dual scenarios carry two faults. No cluster needed.
"""

from __future__ import annotations

from contracts import FaultClass
from testbed.harness.catalog import CATALOG, all_scenarios, get


def test_all_scenarios_present():
    assert set(CATALOG) == {"S0", "S1", "S2", "S3", "S4", "S5", "D1", "D2", "D3"}


def test_crd_files_exist():
    for s in all_scenarios():
        assert s.crd_path.exists(), f"missing CRD for {s.id}: {s.crd_path}"


def test_fault_classes_valid():
    valid = {c.value for c in FaultClass}
    for s in all_scenarios():
        for f in s.faults:
            assert f.fault_class in valid, f"{s.id}: bad fault_class {f.fault_class}"


def test_dual_scenarios_have_two_faults():
    for sid in ("D1", "D2", "D3"):
        s = get(sid)
        assert s.dual and len(s.faults) == 2


def test_single_scenarios_have_one_fault():
    for sid in ("S0", "S1", "S2", "S3", "S4", "S5"):
        s = get(sid)
        assert not s.dual and len(s.faults) == 1


def test_s0_is_flagd_special():
    assert get("S0").special == "flagd"
