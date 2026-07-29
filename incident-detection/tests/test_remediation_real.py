"""Unit tests for typed plans, policy, and SLO gate evaluation."""

from __future__ import annotations

from remediation.plan_schema import ExactTarget, PlanChange, SLOGate, TypedPlanSpec, fingerprint_payload
from remediation.policy import check_approve_identity, check_plan_executable, kill_switch_active
from remediation.slo import SLOSnapshot, evaluate_gates
from runtime.profile import load_profile


def test_fingerprint_stable():
    assert fingerprint_payload({"a": 1}) == fingerprint_payload({"a": 1})


def test_plan_requires_inverse():
    try:
        TypedPlanSpec.from_dict(
            {
                "action": "config_patch",
                "fault_class": "Config",
                "target": {"cluster": "c", "namespace": "otel-demo", "kind": "ConfigMap", "name": "flagd-config"},
                "before_fingerprint": "x",
                "change": {"description": "x", "patch": {"flag_key": "a"}, "inverse": {}},
                "risk_tier": "low",
                "required_approvals": 1,
                "slo_gates": {},
                "incident_id": "i",
                "candidate_node": "flagd",
                "confidence": 0.5,
            }
        )
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("CAUSA_REMEDIATION_KILL_SWITCH", "1")
    d = kill_switch_active()
    assert not d.allowed


def test_approval_token(monkeypatch):
    monkeypatch.setenv("REMEDIATION_APPROVAL_TOKEN", "secret")
    assert not check_approve_identity(None).allowed
    assert check_approve_identity("Bearer secret").allowed


def test_evaluate_gates_fail_closed():
    snap = SLOSnapshot(
        service="payment",
        error_rate=None,
        latency_p99_ms=None,
        success_rate=None,
        fresh=False,
        queried_at="now",
        error="missing",
    )
    out = evaluate_gates(snap, error_rate_max=0.05, latency_p99_ms_max=800, success_rate_min=0.95, require_fresh=True)
    assert out["pass"] is False


def test_profile_capability_denies_unsupported_action(monkeypatch):
    load_profile.cache_clear()
    monkeypatch.setenv("CAUSA_PROFILE", "generic")
    monkeypatch.setenv("REMEDIATION_NAMESPACE", "default")
    monkeypatch.setenv("REMEDIATION_ALLOWED_NAMESPACES", "default")
    spec = TypedPlanSpec(
        action="code_fix_pr",
        fault_class="AppLogic",
        target=ExactTarget(cluster="c", namespace="default", kind="GitHubPullRequest", name="cart"),
        before_fingerprint=fingerprint_payload({"x": 1}),
        change=PlanChange(description="d", patch={"path": "x"}, inverse={"action": "close_pr"}),
        risk_tier="high",
        required_approvals=1,
        slo_gates=SLOGate(require_fresh=False),
        incident_id="i",
        candidate_node="cart",
        confidence=0.8,
    )
    out = check_plan_executable(spec)
    assert out.allowed is False
    assert out.code == "capability_denied"
    load_profile.cache_clear()


def test_uncalibrated_auto_override_is_strictly_local_and_allowlisted(monkeypatch):
    load_profile.cache_clear()
    monkeypatch.setenv("CAUSA_PROFILE", "otel-demo")
    monkeypatch.setenv("CAUSA_ENVIRONMENT", "local-kind")
    monkeypatch.setenv("CAUSA_DEMO_ALLOW_UNCALIBRATED_AUTO_APPLY", "1")
    monkeypatch.setenv("CAUSA_REMEDIATION_AUTO_ACTIONS", "config_patch")
    spec = TypedPlanSpec(
        action="config_patch",
        fault_class="Config",
        target=ExactTarget(
            cluster="kind-gcp-demo-test",
            namespace="otel-demo",
            kind="ConfigMap",
            name="flagd-config",
        ),
        before_fingerprint=fingerprint_payload({"x": 1}),
        change=PlanChange(
            description="demo",
            patch={"flag_key": "productCatalogFailure", "enabled": False},
            inverse={"flag_key": "productCatalogFailure", "enabled": True},
        ),
        risk_tier="low",
        required_approvals=1,
        slo_gates=SLOGate(require_fresh=False),
        incident_id="demo-incident",
        candidate_node="flagd",
        confidence=0.8,
    )
    assert check_plan_executable(spec, for_auto=True).allowed

    monkeypatch.setenv("CAUSA_ENVIRONMENT", "production")
    load_profile.cache_clear()
    denied = check_plan_executable(spec, for_auto=True)
    assert denied.allowed is False
    assert denied.code == "uncalibrated"
    load_profile.cache_clear()
