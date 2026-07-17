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


def test_s3_crd_label_matches_catalog_and_pairs_a_real_fault():
    # Audit H4: the CRD label must not contradict the catalog (was "Dependency").
    # Audit H3: S3 must inject a concurrent real fault alongside the clock skew,
    # else FirstDeviationTime has no anomaly to order under the skew.
    s3 = get("S3")
    text = s3.crd_path.read_text()
    assert "fault-class: Dependency" not in text
    assert text.count("causa/fault-class: Timing") >= 1
    kinds = {d["kind"] for d in rs._crd_documents(s3.crd_path)}
    assert "TimeChaos" in kinds                             # the skew
    assert kinds & {"StressChaos", "NetworkChaos"}          # a concurrent real fault


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


def test_s5_node_override_patches_applied_selector():
    # Regression: --node must repoint the CRD selector actually applied, not just
    # the recorded true_root_node — else the harness stresses one node and records
    # another (audit H2). Object kind/name/namespace must survive so status polling
    # still matches the applied object.
    s5 = get("S5")
    rendered = rs._render_crd(s5.crd_path, "prod-node-7")
    assert "kind-worker" not in rendered
    assert '- "prod-node-7"' in rendered

    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write(rendered)
        tmp = Path(fh.name)
    doc = rs._crd_documents(tmp)[0]
    assert doc["nodes"] == ["prod-node-7"]                 # applied selector repointed
    assert (doc["kind"], doc["name"], doc["namespace"]) == ("StressChaos", "s5-node-stress", "otel-demo")


def test_render_crd_verbatim_without_override():
    # No --node → the manifest must pass through untouched (multi-doc D1–D3 safety).
    s5 = get("S5")
    assert rs._render_crd(s5.crd_path, None) == s5.crd_path.read_text()
    d1 = get("D1")
    assert rs._render_crd(d1.crd_path, None) == d1.crd_path.read_text()


def test_render_crd_noop_on_non_node_crd():
    # --node on a scenario with no `nodes:` selector is a logged no-op, not a corruption.
    s2 = get("S2")
    assert rs._render_crd(s2.crd_path, "prod-node-7") == s2.crd_path.read_text()


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
