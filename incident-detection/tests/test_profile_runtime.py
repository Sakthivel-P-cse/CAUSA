from __future__ import annotations

from runtime import profile as runtime_profile


def test_load_default_profile():
    runtime_profile.load_profile.cache_clear()
    p = runtime_profile.load_profile("otel-demo")
    assert p.name == "otel-demo"
    assert "otel-demo" in p.namespaces
    assert p.configmap_name == "flagd-config"


def test_generic_profile_has_wildcard_capabilities():
    runtime_profile.load_profile.cache_clear()
    p = runtime_profile.load_profile("generic")
    assert p.remediation_namespace == "default"
    assert "scale_up" in p.capabilities.get("*", [])


def test_env_override_namespace(monkeypatch):
    runtime_profile.load_profile.cache_clear()
    monkeypatch.setenv("CAUSA_PROFILE", "generic")
    monkeypatch.setenv("REMEDIATION_NAMESPACE", "custom-ns")
    monkeypatch.setenv("REMEDIATION_ALLOWED_NAMESPACES", "custom-ns,ops")
    p = runtime_profile.load_profile()
    assert p.remediation_namespace == "custom-ns"
    assert "ops" in p.namespaces

