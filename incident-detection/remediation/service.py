"""Propose orchestration for remediation plans (apply runs in the worker)."""

from __future__ import annotations

import logging
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
from remediation.nim_client import (
    classify_with_nim,
    merge_fault_class,
    nim_health,
    normalize_prompt_mode,
    prompt_hash,
    propose_applogic_patch_with_nim,
    propose_ops_guidance_with_nim,
    sanitize_operator_prompt,
)
from remediation.policy import check_plan_executable
from remediation.plan_schema import TypedPlanSpec
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


def propose_for_report(
    report: dict[str, Any],
    *,
    manual_prompt: str | None = None,
    prompt_mode: str | None = None,
    regen_of_plan_id: str | None = None,
    parent_plan_id: str | None = None,
    regen_reason: str | None = None,
) -> dict[str, Any]:
    ranked = ranked_from_report(report)
    if not ranked.candidates:
        return {"ok": False, "error": "no origin candidates", "plan": None}

    from remediation.policy import allowlist

    allowed = allowlist()
    top = None
    for cand in ranked.candidates:
        if cand.node_id in allowed:
            top = cand
            break
    if top is None:
        top = ranked.candidates[0]
        for key in ("root_cause_service", "alerted_service"):
            name = str(report.get(key) or "")
            if name in allowed:
                for cand in ranked.candidates:
                    if cand.node_id == name:
                        top = cand
                        break
                break

    operator_prompt = sanitize_operator_prompt(manual_prompt)
    mode = normalize_prompt_mode(prompt_mode)
    has_operator = bool(operator_prompt)
    nim_ctx = build_nim_context(report, ranked)

    rules = FaultClassifier().classify(
        top,
        alert_name=str(report.get("alert_name") or "") or None,
        rca_failure_type=report.get("failure_type"),
    )
    nim = classify_with_nim(
        nim_ctx,
        manual_prompt=operator_prompt or None,
        prompt_mode=mode if has_operator else None,
    )
    chosen, source = merge_fault_class(
        nim=nim,
        rules_fault_class=rules.fault_class,
        rca_failure_type=report.get("failure_type"),
    )
    if has_operator and source == "nim":
        source = "nim_manual_prompt"
    elif has_operator and nim.ok:
        source = f"{source}+nim_manual_prompt"
    incident_id = str(report.get("incident_id") or report.get("id") or "")
    plan: RemediationPlan = RemediationRouter().route(chosen, top, ranked, incident_id=incident_id)

    details = dict(plan.details or {})
    details["source"] = source
    details["rules_fault_class"] = rules.fault_class.value
    details["rules_rationale"] = rules.rationale
    details["nim"] = nim.to_dict()
    details["rationale"] = nim.rationale or rules.rationale
    details["operator_remediation"] = nim.remediation or plan.summary
    if has_operator:
        details["operator_prompt"] = operator_prompt
        details["prompt_mode"] = mode
        details["prompt_hash"] = prompt_hash(operator_prompt)
        details["nim_prompt_source"] = "nim_manual_prompt"
    else:
        details["nim_prompt_source"] = "nim_default"
    parent_id = parent_plan_id or regen_of_plan_id
    if regen_of_plan_id:
        details["regen_of_plan_id"] = regen_of_plan_id
    if parent_id:
        details["parent_plan_id"] = parent_id
    if regen_reason:
        details["regen_reason"] = str(regen_reason)[:500]

    if chosen == FaultClass.APP_LOGIC:
        patch = propose_applogic_patch_with_nim(
            nim_ctx,
            manual_prompt=operator_prompt or None,
            prompt_mode=mode if has_operator else None,
        )
        if patch:
            details["nim_patch"] = patch
        from remediation.branches.applogic import propose_code_fix_pr

        refreshed = propose_code_fix_pr(
            top, ranked, incident_id=incident_id, nim_patch=patch if patch else None
        )
        details.update(refreshed)
        plan.summary = refreshed.get("solution", {}).get("summary") or plan.summary
    elif chosen in (FaultClass.RESOURCE, FaultClass.INFRA, FaultClass.DATA):
        guidance = propose_ops_guidance_with_nim(
            nim_ctx,
            fault_class=chosen.value,
            planned_action=plan.action,
            manual_prompt=operator_prompt or None,
            prompt_mode=mode if has_operator else None,
        )
        solution = dict(details.get("solution") or {})
        if not solution.get("summary"):
            solution["summary"] = plan.summary
        if guidance:
            details["nim_ops_guidance"] = guidance
            details["ai_summary"] = guidance.get("summary") or ""
            details["fix_recommendation"] = guidance.get("recommendation") or ""
            details["operator_remediation"] = (
                guidance.get("recommendation")
                or details.get("operator_remediation")
                or plan.summary
            )
            if guidance.get("summary"):
                details["rationale"] = guidance["summary"]
            solution["ai_summary"] = guidance.get("summary") or ""
            solution["recommendation"] = guidance.get("recommendation") or ""
            solution["steps"] = guidance.get("steps") or []
            solution["risks"] = guidance.get("risks") or ""
            solution["verify"] = guidance.get("verify") or ""
            if guidance.get("recommendation"):
                solution["summary"] = guidance["recommendation"]
                plan.summary = guidance["recommendation"]
        elif nim.ok and (nim.rationale or nim.remediation):
            details["ai_summary"] = nim.rationale or ""
            details["fix_recommendation"] = nim.remediation or ""
            solution["ai_summary"] = nim.rationale or ""
            solution["recommendation"] = nim.remediation or ""
            if nim.remediation:
                solution["summary"] = nim.remediation
                plan.summary = nim.remediation
        details["solution"] = solution

    plan_spec = details.get("plan_spec")
    if isinstance(plan_spec, dict):
        details["plan_spec"] = plan_spec
    policy = None
    if isinstance(plan_spec, dict):
        try:
            spec = TypedPlanSpec.from_dict(plan_spec)
            policy = check_plan_executable(spec)
            details["policy"] = policy.to_dict()
        except Exception as exc:  # noqa: BLE001
            details["policy"] = {"allowed": False, "reason": str(exc), "code": "invalid_plan_spec"}

    solution = details.get("solution") or details.get("patch") or details.get("rollback") or {}

    out_plan = {
        "incident_id": incident_id,
        "report_id": str(report.get("id") or report.get("incident_id") or ""),
        "candidate_node": top.node_id,
        "fault_class": plan.fault_class.value,
        "action": plan.action,
        "depth": plan.depth,
        "summary": plan.summary,
        "details": details,
        "plan_spec": plan_spec if isinstance(plan_spec, dict) else None,
        "before_fingerprint": details.get("before_fingerprint")
        or (plan_spec.get("before_fingerprint") if isinstance(plan_spec, dict) else None),
        "idempotency_key": (plan_spec or {}).get("idempotency_key") if isinstance(plan_spec, dict) else None,
        "expires_at": (plan_spec or {}).get("expires_at") if isinstance(plan_spec, dict) else None,
        "risk_tier": (plan_spec or {}).get("risk_tier") if isinstance(plan_spec, dict) else None,
        "solution": solution,
        "source": source,
        "nim_ok": nim.ok,
        "policy": policy.to_dict() if policy else details.get("policy"),
    }
    if parent_id and out_plan.get("idempotency_key"):
        out_plan["idempotency_key"] = f"{out_plan['idempotency_key']}:regen:{prompt_hash(parent_id + (operator_prompt or ''))}"

    return {
        "ok": True,
        "error": None,
        "plan": out_plan,
        "nim_health": nim_health(),
    }


def apply_plan_v1(plan_row: dict[str, Any], *, breach: bool = False) -> dict[str, Any]:
    """Deprecated: execution moved to remediation.worker. Kept for test imports."""
    return {
        "attempted": False,
        "mode": "worker_required",
        "status": "failed",
        "error": "apply_plan_v1 disabled; approve plans and let the remediation worker execute",
        "breach_ignored": breach,
    }
