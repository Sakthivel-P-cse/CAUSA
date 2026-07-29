"""Propose path with NIM disabled (rules-only)."""

from __future__ import annotations

from remediation.service import propose_for_report


def _report():
    return {
        "incident_id": "inc-demo-1",
        "id": "inc-demo-1",
        "alert_name": "PodCrashLooping",
        "alerted_service": "payment",
        "root_cause_service": "payment",
        "root_cause_summary": "payment is the top origin candidate",
        "failure_type": None,
        "confidence_score": 0.7,
        "origin_candidates": [
            {
                "node_id": "payment",
                "node_kind": "service",
                "score": 0.9,
                "confidence": 0.7,
                "role": "origin",
                "signals": {"T": 1.0, "D": 0.8, "P": 0.9},
                "dev_vector": {"error_rate": 0.6},
                "explanation": "payment panic exception stack trace",
            }
        ],
        "timeline": [],
        "blast_radius": [],
    }


def test_propose_rules_only(monkeypatch):
    monkeypatch.setenv("CAUSA_NIM_ENABLED", "0")
    result = propose_for_report(_report())
    assert result["ok"] is True
    assert result["plan"]["candidate_node"] == "payment"
    assert result["plan"]["fault_class"] in {
        "AppLogic",
        "Config",
        "Infra",
        "Dependency",
        "Resource",
        "Data",
    }
    assert result["plan"]["details"]["source"] in ("rules", "rca", "nim")
    assert result["plan"]["details"]["nim_prompt_source"] == "nim_default"


def test_propose_persists_operator_prompt_metadata(monkeypatch):
    monkeypatch.setenv("CAUSA_NIM_ENABLED", "0")
    result = propose_for_report(
        _report(),
        manual_prompt="Prefer circuit breaker; avoid schema changes",
        prompt_mode="constraints",
        regen_of_plan_id="plan-parent-1",
        regen_reason="not convincing",
    )
    assert result["ok"] is True
    details = result["plan"]["details"]
    assert details["operator_prompt"].startswith("Prefer circuit breaker")
    assert details["prompt_mode"] == "constraints"
    assert details["prompt_hash"]
    assert details["regen_of_plan_id"] == "plan-parent-1"
    assert details["parent_plan_id"] == "plan-parent-1"
    assert details["regen_reason"] == "not convincing"
    assert details["nim_prompt_source"] == "nim_manual_prompt"


def test_resource_plan_gets_ops_ai_guidance(monkeypatch):
    monkeypatch.setenv("CAUSA_NIM_ENABLED", "1")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")

    from contracts import FaultClass
    from remediation import service as rem_service
    from remediation.nim_client import NimClassifyResult

    monkeypatch.setattr(
        rem_service,
        "classify_with_nim",
        lambda *a, **k: NimClassifyResult(
            fault_class=FaultClass.RESOURCE,
            confidence=0.9,
            rationale="memory near limit",
            remediation="scale up",
            raw={},
            ok=True,
            model="test",
        ),
    )
    monkeypatch.setattr(
        rem_service,
        "propose_ops_guidance_with_nim",
        lambda *a, **k: {
            "summary": "Quote is saturating memory under load.",
            "recommendation": "Scale quote replicas and raise memory limit cautiously.",
            "steps": ["Confirm memory metrics", "Scale one step", "Watch error rate"],
            "risks": "Brief cold-start latency",
            "verify": "Memory < 70% and alert clears",
            "model": "test",
        },
    )
    monkeypatch.setattr(
        rem_service,
        "propose_applogic_patch_with_nim",
        lambda *a, **k: None,
    )

    report = _report()
    report["alert_name"] = "ContainerMemoryNearLimit"
    report["alerted_service"] = "quote"
    report["root_cause_service"] = "quote"
    report["failure_type"] = "Resource"
    report["origin_candidates"][0]["node_id"] = "quote"
    report["origin_candidates"][0]["explanation"] = "memory near limit"

    result = propose_for_report(report)
    assert result["ok"] is True
    details = result["plan"]["details"]
    assert details["ai_summary"].startswith("Quote is saturating")
    assert "Scale quote" in details["fix_recommendation"]
    assert details["solution"]["steps"][0].startswith("Confirm")
    assert details["nim_ops_guidance"]["verify"].startswith("Memory")
