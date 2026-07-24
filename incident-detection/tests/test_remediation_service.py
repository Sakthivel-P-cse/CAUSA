"""Propose path with NIM disabled (rules-only)."""

from __future__ import annotations

from remediation.service import propose_for_report


def test_propose_rules_only(monkeypatch):
    monkeypatch.setenv("CAUSA_NIM_ENABLED", "0")
    report = {
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
    result = propose_for_report(report)
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
