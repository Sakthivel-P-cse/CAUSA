"""Unit tests for NIM client merge + JSON extraction (no live network)."""

from __future__ import annotations

import os

from contracts import FaultClass
from remediation.nim_client import (
    _extract_json,
    _parse_fault_class,
    merge_fault_class,
    nim_enabled,
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
