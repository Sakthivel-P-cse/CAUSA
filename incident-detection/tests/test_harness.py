"""
Ground-truth harness — CRD parsing, S5 node substitution (#4), dry-run
no-persist, collision-free ids, injection detection (#6), and catalog fidelity.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from contracts import FaultClass, Window

from testbed.harness import run_scenario as rs
from testbed.harness.catalog import (
    CLASSIFICATION_TARGETS,
    COVERED_FAULT_CLASSES,
    UNCOVERED_TARGETS,
    all_scenarios,
    get,
)


def test_s3_fault_class_is_timing_not_dependency():
    assert get("S3").faults[0].fault_class == "Timing"


def test_every_scenario_fault_class_is_a_valid_faultclass():
    # A scenario can only carry a real FaultClass value — guards typos in the
    # catalog's raw strings against the contract enum.
    valid = {c.value for c in FaultClass}
    for s in all_scenarios():
        for f in s.faults:
            assert f.fault_class in valid, f"{s.id}: unknown fault_class {f.fault_class!r}"


def test_config_and_data_are_documented_v1_gaps():
    # Audit M3: the catalog produces no Config or Data scenario. This is an
    # ACCEPTED V1 limitation, codified here so it can't silently become a claim
    # of full classifier coverage. Closing the gap = adding such a scenario,
    # which flips this assertion and forces a conscious doc update.
    assert UNCOVERED_TARGETS == {"Config", "Data"}
    assert "Config" not in COVERED_FAULT_CLASSES
    assert "Data" not in COVERED_FAULT_CLASSES
    # The four turnkey targets ARE covered; Timing is carried but not a target.
    assert {"AppLogic", "Resource", "Dependency", "Infra"} <= COVERED_FAULT_CLASSES
    assert "Timing" not in CLASSIFICATION_TARGETS


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


def test_single_fault_can_use_real_pipeline_incident_id():
    now = datetime.now(timezone.utc)
    incident = rs._build_incidents(
        get("S1"),
        now,
        now,
        None,
        incident_id_override="inc-from-alertmanager",
    )[0]
    assert incident.incident_id == "inc-from-alertmanager"


def test_incident_id_override_rejects_dual_fault_scenario():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="single-fault"):
        rs._build_incidents(
            get("D1"),
            now,
            now,
            None,
            incident_id_override="ambiguous",
        )


def test_dry_run_returns_without_persisting(tmp_path):
    incs = rs.run_scenario(get("S1"), apply=False, out_dir=tmp_path, hold_seconds=1)
    assert incs                                          # built for inspection
    assert list(tmp_path.iterdir()) == []               # but nothing persisted


def test_injection_detection():
    assert rs._is_injected('{"status":{"conditions":[{"type":"AllInjected","status":"True"}]}}')
    assert not rs._is_injected('{"status":{"conditions":[{"type":"Selected","status":"True"}]}}')
    assert not rs._is_injected("not json")


# ── M2: flagd flip + observable-effect gate ───────────────────────────

_FLAGD_FILE = json.dumps({
    "flags": {
        "productCatalogFailure": {
            "state": "ENABLED",
            "variants": {"on": True, "off": False},
            "defaultVariant": "off",
        }
    }
})


def test_set_flagd_variant_flips_only_default_variant():
    out = rs._set_flagd_variant(_FLAGD_FILE, "productCatalogFailure", "on")
    doc = json.loads(out)
    assert doc["flags"]["productCatalogFailure"]["defaultVariant"] == "on"
    assert doc["flags"]["productCatalogFailure"]["variants"] == {"on": True, "off": False}  # untouched


def test_read_flagd_variant_roundtrip():
    assert rs._read_flagd_variant(_FLAGD_FILE, "productCatalogFailure") == "off"
    assert rs._read_flagd_variant(_FLAGD_FILE, "noSuchFlag") is None
    assert rs._read_flagd_variant("not json", "x") is None


def test_set_flagd_variant_rejects_unknown_flag_or_variant():
    with pytest.raises(KeyError):
        rs._set_flagd_variant(_FLAGD_FILE, "noSuchFlag", "on")
    with pytest.raises(KeyError):
        rs._set_flagd_variant(_FLAGD_FILE, "productCatalogFailure", "maybe")


def _win():
    return Window(datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
                  datetime(2026, 7, 16, 12, 10, tzinfo=timezone.utc))


def test_effect_observed_true_on_error_spike(fake_adapter_cls):
    adapter = fake_adapter_cls({("product-catalog", "error_rate"): [[0, 0.0], [1, 0.4]]})
    assert rs._effect_observed(adapter, ["product-catalog"], _win(), error_floor=0.05)


def test_effect_observed_true_on_latency_rise(fake_adapter_cls):
    adapter = fake_adapter_cls({("checkout", "p95_latency"): [[0, 50.0], [1, 55.0], [2, 400.0]]})
    assert rs._effect_observed(adapter, ["checkout"], _win(), error_floor=0.05)


def test_effect_not_observed_is_a_phantom(fake_adapter_cls):
    # flat error_rate near zero + flat latency → nothing actually broke.
    adapter = fake_adapter_cls({
        ("product-catalog", "error_rate"): [[0, 0.0], [1, 0.001]],
        ("product-catalog", "p95_latency"): [[0, 50.0], [1, 51.0]],
    })
    assert not rs._effect_observed(adapter, ["product-catalog"], _win(), error_floor=0.05)


def test_injection_ok_decision_matrix():
    ok = rs._injection_ok
    # flagd: no AllInjected signal → require an observed effect
    assert ok(None, None, is_flagd=True, allow_unverified=False) is False     # no --verify-effect
    assert ok(None, True, is_flagd=True, allow_unverified=False) is True
    assert ok(None, False, is_flagd=True, allow_unverified=False) is False
    assert ok(None, None, is_flagd=True, allow_unverified=True) is True        # override
    # CRD: require AllInjected; if effect was probed, require it too
    assert ok(True, None, is_flagd=False, allow_unverified=False) is True
    assert ok(False, None, is_flagd=False, allow_unverified=False) is False
    assert ok(True, True, is_flagd=False, allow_unverified=False) is True
    assert ok(True, False, is_flagd=False, allow_unverified=False) is False


def test_target_nodes_resolves_infra_sentinel():
    assert rs._target_nodes(get("S0"), None) == ["product-catalog"]
    assert rs._target_nodes(get("S5"), None) == ["kind-worker"]          # from CRD selector
    assert rs._target_nodes(get("S5"), "node-7") == ["node-7"]           # --node override
    assert rs._target_nodes(get("D1"), None) == ["currency", "email"]


def test_flagd_scenario_refuses_to_record_without_verification(tmp_path):
    # Audit M2: S0 through the harness with neither --verify-effect nor
    # --allow-unverified must NOT persist a ground-truth row (phantom guard).
    # Returns [] before touching kubectl, so this needs no cluster.
    incs = rs.run_scenario(get("S0"), out_dir=tmp_path, hold_seconds=1)
    assert incs == []
    assert list(tmp_path.iterdir()) == []
