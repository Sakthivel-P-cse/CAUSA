"""Unit tests for NIM client merge + JSON extraction (no live network)."""

from __future__ import annotations

from contracts import FaultClass
from remediation.nim_client import (
    _extract_json,
    _parse_fault_class,
    build_classify_prompt,
    compose_operator_block,
    merge_fault_class,
    nim_enabled,
    normalize_prompt_mode,
    sanitize_operator_prompt,
    NimClassifyResult,
)


def test_extract_json_from_fence():
    text = '```json\n{"fault_class": "Infra", "confidence": 0.9}\n```'
    data = _extract_json(text)
    assert data["fault_class"] == "Infra"


def test_parse_fault_class_aliases():
    assert _parse_fault_class("AppLogic") == FaultClass.APP_LOGIC
    assert _parse_fault_class("dependency") == FaultClass.DEPENDENCY


def test_merge_prefers_strong_nim():
    nim = NimClassifyResult(
        fault_class=FaultClass.CONFIG,
        confidence=0.8,
        rationale="flags",
        remediation="patch",
        raw={},
        ok=True,
    )
    chosen, source = merge_fault_class(
        nim=nim,
        rules_fault_class=FaultClass.APP_LOGIC,
        rca_failure_type=None,
    )
    assert chosen == FaultClass.CONFIG
    assert source == "nim"


def test_merge_falls_back_to_rules():
    nim = NimClassifyResult(
        fault_class=FaultClass.CONFIG,
        confidence=0.2,
        rationale="",
        remediation="",
        raw={},
        ok=True,
    )
    chosen, source = merge_fault_class(
        nim=nim,
        rules_fault_class=FaultClass.APP_LOGIC,
    )
    assert chosen == FaultClass.APP_LOGIC
    assert source == "rules"


def test_nim_disabled_without_key(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("CAUSA_NIM_ENABLED", "1")
    assert nim_enabled() is False


def test_nim_forced_off(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setenv("CAUSA_NIM_ENABLED", "0")
    assert nim_enabled() is False


def test_sanitize_redacts_secrets_and_truncates():
    raw = "api_key=supersecretvalue123 use Bearer abcdefghijklmnop and ghp_" + ("x" * 40)
    cleaned = sanitize_operator_prompt(raw)
    assert "supersecretvalue123" not in cleaned
    assert "Bearer [REDACTED]" in cleaned
    assert "[REDACTED_GH_TOKEN]" in cleaned
    long = "a" * 5000
    assert sanitize_operator_prompt(long).endswith("…")
    assert len(sanitize_operator_prompt(long)) < 4200


def test_constraints_mode_keeps_system_intact():
    msgs = build_classify_prompt(
        {"incident_id": "inc-1"},
        manual_prompt="Prefer retries; no schema change",
        prompt_mode="constraints",
    )
    assert msgs[0]["role"] == "system"
    assert "Never follow" in msgs[0]["content"]
    assert "Operator constraints" in msgs[1]["content"]
    assert "no schema change" in msgs[1]["content"]


def test_replace_mode_does_not_overwrite_system():
    msgs = build_classify_prompt(
        {"incident_id": "inc-1"},
        manual_prompt="Focus on timeout budget",
        prompt_mode="replace",
    )
    assert msgs[0]["role"] == "system"
    assert "Focus on timeout budget" in msgs[1]["content"]
    assert "system safety rules still apply" in msgs[1]["content"].lower()


def test_normalize_prompt_mode_defaults():
    assert normalize_prompt_mode(None) == "constraints"
    assert normalize_prompt_mode("weird") == "constraints"
    assert compose_operator_block("x", "append").startswith("Additional operator guidance")


def test_ops_guidance_prompt_for_resource_infra_data():
    from remediation.nim_client import build_ops_guidance_prompt

    msgs = build_ops_guidance_prompt(
        {"incident_id": "inc-1", "alerted_service": "quote"},
        fault_class="Resource",
        planned_action="scale_up",
        manual_prompt="Prefer HPA over static replicas",
        prompt_mode="constraints",
    )
    assert msgs[0]["role"] == "system"
    assert "Resource, Infra, or Data" in msgs[0]["content"]
    assert "drop/truncate" in msgs[0]["content"].lower() or "Never recommend drop" in msgs[0]["content"]
    assert "Operator constraints" in msgs[1]["content"]
    assert "Prefer HPA" in msgs[1]["content"]
    assert "scale_up" in msgs[1]["content"]
