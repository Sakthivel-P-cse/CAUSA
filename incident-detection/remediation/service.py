"""Propose / approve / apply orchestration for Dev C remediation plans."""

from __future__ import annotations

import logging
import os
from typing import Any

from classify.classifier import FaultClassifier
from contracts import (
    FaultClass,
    NodeKind,
    RankedResult,
    Role,
    RootCauseCandidate,
    SignalScores,
)
from remediation.canary import SimulatedCanary
from remediation.nim_client import (
    classify_with_nim,
    merge_fault_class,
    nim_health,
    propose_applogic_patch_with_nim,
)
from remediation.router import RemediationPlan, RemediationRouter

logger = logging.getLogger(__name__)


def _signals_from(raw: Any) -> SignalScores:
    if isinstance(raw, SignalScores):
        return raw
    if isinstance(raw, dict):
        return SignalScores(
            T=float(raw.get("T") or raw.get("t") or 0.0),
            D=float(raw.get("D") or raw.get("d") or 0.0),
            P=float(raw.get("P") or raw.get("p") or 0.0),
        )
    return SignalScores()


def _role_from(raw: Any) -> Role:
    try:
        return Role(str(raw))
    except Exception:
        return Role.UNKNOWN


def _kind_from(raw: Any) -> NodeKind:
    try:
        return NodeKind(str(raw))
    except Exception:
        return NodeKind.SERVICE


def candidate_from_dict(d: dict[str, Any]) -> RootCauseCandidate:
    signals = d.get("signals") or {}
    return RootCauseCandidate(
        node_id=str(d.get("node_id") or d.get("node") or "unknown"),
        node_kind=_kind_from(d.get("node_kind") or "service"),
        score=float(d.get("score") or 0.0),
        confidence=float(d.get("confidence") or 0.0),
        role=_role_from(d.get("role") or "unknown"),
        signals=_signals_from(signals),
        dev_vector=dict(d.get("dev_vector") or {}),
        explanation=str(d.get("explanation") or ""),
        flags=dict(d.get("flags") or {}),
    )


def ranked_from_report(report: dict[str, Any]) -> RankedResult:
    raw_cands = report.get("origin_candidates") or []
    candidates = [
        candidate_from_dict(c) if isinstance(c, dict) else c
        for c in raw_cands
    ]
    seed = report.get("root_cause_service") or report.get("alerted_service")
    if not candidates and seed:
        candidates = [
            RootCauseCandidate(
                node_id=str(seed),
                node_kind=NodeKind.SERVICE,
                score=float(report.get("confidence_score") or 0.5) or 0.5,
                confidence=float(report.get("confidence_score") or 0.5) or 0.5,
                role=Role.ORIGIN,
                signals=SignalScores(T=1.0, D=0.8, P=0.8),
                explanation=str(report.get("root_cause_summary") or f"alerted service {seed}"),
                flags={"synthetic_from_alert": True},
            )
        ]
    return RankedResult(
        candidates=candidates,
        incident_id=str(report.get("incident_id") or report.get("id") or ""),
        fault_class=_parse_optional_fc(report.get("failure_type")),
        abstain_reason=(
            str(report.get("root_cause_summary") or "").replace("RCA abstained: ", "")
            if str(report.get("root_cause_summary") or "").lower().startswith("rca abstained")
            else None
        ),
    )


def _parse_optional_fc(value: Any) -> FaultClass | None:
    if not value:
        return None
    try:
        return FaultClass(str(value))
    except Exception:
        return None


def build_nim_context(report: dict[str, Any], ranked: RankedResult) -> dict[str, Any]:
    return {
        "incident_id": report.get("incident_id") or report.get("id"),
        "alert_name": report.get("alert_name"),
        "alerted_service": report.get("alerted_service"),
        "root_cause_service": report.get("root_cause_service"),
        "root_cause_summary": report.get("root_cause_summary"),
        "failure_type": report.get("failure_type"),
        "confidence_score": report.get("confidence_score"),
        "candidates": [
            {
                "node_id": c.node_id,
                "role": c.role.value,
                "node_kind": c.node_kind.value,
                "score": c.score,
                "confidence": c.confidence,
                "explanation": (c.explanation or "")[:400],
                "dev_vector": c.dev_vector,
            }
            for c in ranked.candidates[:5]
        ],
        "timeline": (report.get("timeline") or [])[:8],
        "blast_radius": report.get("blast_radius") or [],
    }


def propose_for_report(report: dict[str, Any]) -> dict[str, Any]:
    """Classify + route one plan for the top candidate. Returns serializable plan dict."""
    ranked = ranked_from_report(report)
    if not ranked.candidates:
        return {
            "ok": False,
            "error": "no origin candidates",
            "plan": None,
        }

    top = ranked.candidates[0]
    rules = FaultClassifier().classify(
        top,
        alert_name=str(report.get("alert_name") or "") or None,
        rca_failure_type=report.get("failure_type"),
    )
    nim = classify_with_nim(build_nim_context(report, ranked))
    chosen, source = merge_fault_class(
        nim=nim,
        rules_fault_class=rules.fault_class,
        rca_failure_type=report.get("failure_type"),
    )
    plan: RemediationPlan = RemediationRouter().route(chosen, top, ranked)

    details = dict(plan.details or {})
    details["source"] = source
    details["rules_fault_class"] = rules.fault_class.value
    details["rules_rationale"] = rules.rationale
    details["nim"] = nim.to_dict()
    details["rationale"] = nim.rationale or rules.rationale
    details["operator_remediation"] = nim.remediation or plan.summary

    if chosen == FaultClass.APP_LOGIC:
        patch = propose_applogic_patch_with_nim(build_nim_context(report, ranked))
        if patch:
            details["nim_patch"] = patch

    return {
        "ok": True,
        "error": None,
        "plan": {
            "incident_id": str(report.get("incident_id") or report.get("id") or ""),
            "report_id": str(report.get("id") or report.get("incident_id") or ""),
            "candidate_node": top.node_id,
            "fault_class": plan.fault_class.value,
            "action": plan.action,
            "depth": plan.depth,
            "summary": plan.summary,
            "details": details,
            "source": source,
            "nim_ok": nim.ok,
        },
        "nim_health": nim_health(),
    }


def apply_plan_v1(plan_row: dict[str, Any], *, breach: bool = False) -> dict[str, Any]:
    """v1 apply: SimulatedCanary. Phase 2/3 hooks call real apply when enabled."""
    action = str(plan_row.get("action") or "")
    dry_run = os.getenv("REMEDIATION_DRY_RUN", "0") in ("1", "true", "True")

    # Prefer real applyers when available and not dry-run.
    if not dry_run and not breach:
        if action == "config_patch":
            from remediation.apply.k8s import apply_config_patch

            result = apply_config_patch(plan_row)
            if result.get("attempted"):
                return result
        if action == "rollback_to_last_good":
            from remediation.apply.k8s import apply_dependency_rollback

            result = apply_dependency_rollback(plan_row)
            if result.get("attempted"):
                return result
        if action == "code_fix_pr":
            from remediation.apply.github_pr import open_draft_pr

            result = open_draft_pr(plan_row)
            if result.get("attempted"):
                return result

    metric_fn = SimulatedCanary.breaching_metrics if breach else None
    canary = SimulatedCanary(metric_fn=metric_fn).apply()
    status = "rolled_back" if canary.rolled_back else ("succeeded" if canary.applied else "failed")
    return {
        "attempted": True,
        "mode": "simulated_canary",
        "dry_run": dry_run,
        "status": status,
        "canary": canary.to_dict(),
    }
