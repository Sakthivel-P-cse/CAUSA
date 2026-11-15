"""Durable remediation worker — claims approved plans and executes real backends."""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_ID = Path(__file__).resolve().parents[1]
for p in (_ROOT, _ID):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

logger = logging.getLogger(__name__)


def _worker_id() -> str:
    return os.getenv("REMEDIATION_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


def _store():
    from dashboard.backend.remediation_store import RemediationStore

    return RemediationStore()


def _rebuild_plan_spec(plan_row: dict[str, Any]) -> dict[str, Any] | None:
    details = plan_row.get("details") or {}
    if isinstance(details.get("plan_spec"), dict):
        return details["plan_spec"]
    action = str(plan_row.get("action") or "")
    node = str(plan_row.get("candidate_node") or "")
    incident_id = str(plan_row.get("incident_id") or "")
    try:
        from contracts import NodeKind, Role, RootCauseCandidate, SignalScores
        from remediation.branches.applogic import propose_code_fix_pr
        from remediation.branches.config import propose_config_patch
        from remediation.branches.dependency import propose_rollback
        from remediation.branches.stubs import propose_data_recovery, propose_infra_recovery, propose_resource_scale

        cand = RootCauseCandidate(
            node_id=node or "unknown",
            node_kind=NodeKind.SERVICE,
            score=0.5,
            confidence=0.5,
            role=Role.ORIGIN,
            signals=SignalScores(T=1.0, D=0.5, P=0.5),
            explanation=str(details.get("rationale") or plan_row.get("summary") or ""),
        )
        if action == "code_fix_pr":
            return propose_code_fix_pr(cand, incident_id=incident_id).get("plan_spec")
        if action == "config_patch":
            return propose_config_patch(cand, incident_id=incident_id).get("plan_spec")
        if action == "rollback_to_last_good":
            return propose_rollback(cand, incident_id=incident_id).get("plan_spec")
        if action == "scale_up":
            return propose_resource_scale(cand, incident_id=incident_id).get("plan_spec")
        if action == "scale_restart_cordon":
            return propose_infra_recovery(cand, incident_id=incident_id).get("plan_spec")
        if action == "restart_data_path":
            return propose_data_recovery(cand, incident_id=incident_id).get("plan_spec")
    except Exception as exc:  # noqa: BLE001
        logger.warning("rebuild plan_spec failed: %s", exc)
    return None


def execute_plan(
    plan_row: dict[str, Any],
    store,
    *,
    worker_id: str | None = None,
) -> dict[str, Any]:
    from remediation.plan_schema import TypedPlanSpec
    from remediation.policy import check_plan_executable
    from remediation.slo import evaluate_gates, snapshot_service

    plan_id = str(plan_row.get("id") or "")

    def set_status(status: str, apply_result: dict[str, Any] | None = None):
        if worker_id:
            return store.set_status_if_owned(
                plan_id,
                status,
                worker_id,
                apply_result=apply_result,
            )
        return store.set_status(plan_id, status, apply_result=apply_result)

    raw_spec = plan_row.get("plan_spec") or (plan_row.get("details") or {}).get("plan_spec")
    if not isinstance(raw_spec, dict):
        # Rebuild once from stored details / re-propose path
        rebuilt = _rebuild_plan_spec(plan_row)
        if rebuilt:
            raw_spec = rebuilt
            plan_row["plan_spec"] = rebuilt
            store._update({**plan_row, "plan_spec": rebuilt})
            store.add_event(plan_id, "plan_spec_rebuilt", {"ok": True})
        else:
            result = {
                "attempted": False,
                "status": "failed",
                "error": "missing plan_spec — plan was not executable (re-open incident to auto-propose)",
            }
            set_status("failed", result)
            return result

    try:
        spec = TypedPlanSpec.from_dict(raw_spec)
        if not 0 < spec.slo_gates.window_seconds <= spec.timeout_seconds:
            raise ValueError("observation window must be positive and within plan timeout")
    except Exception as exc:  # noqa: BLE001
        result = {"attempted": False, "status": "failed", "error": f"invalid plan_spec: {exc}"}
        set_status("failed", result)
        return result

    policy = check_plan_executable(spec)
    store.add_event(plan_id, "policy_check", policy.to_dict())
    if not policy.allowed:
        status = "expired" if policy.code == "expired" else "failed"
        result = {"attempted": False, "status": status, "error": policy.reason, "policy": policy.to_dict()}
        set_status(status, result)
        return result

    service = spec.candidate_node
    pre = snapshot_service(service)
    store.add_event(plan_id, "slo_pre", pre.to_dict())
    pre_eval = evaluate_gates(
        pre,
        error_rate_max=spec.slo_gates.error_rate_max * 5,
        latency_p99_ms_max=spec.slo_gates.latency_p99_ms_max * 5,
        success_rate_min=max(0.0, spec.slo_gates.success_rate_min - 0.5),
        require_fresh=spec.slo_gates.require_fresh,
    )
    if spec.slo_gates.require_fresh and pre_eval.get("verified") is False:
        result = {
            "attempted": False,
            "status": "failed",
            "error": f"pre-flight SLO unavailable: {pre_eval['reason']}",
            "slo_pre": pre.to_dict(),
        }
        set_status("failed", result)
        return result

    set_status("executing")
    if worker_id and not store.owns_lease(plan_id, worker_id):
        raise RuntimeError(f"worker {worker_id} lost lease before executing {plan_id}")
    apply_result = _dispatch_execute(plan_row, spec)
    store.add_event(plan_id, "execute", apply_result)
    plan_row["apply_result"] = apply_result
    if not apply_result.get("attempted"):
        set_status("failed", apply_result)
        return apply_result
    if apply_result.get("status") == "failed":
        set_status("failed", apply_result)
        return apply_result

    if spec.action == "code_fix_pr":
        apply_result["status"] = "succeeded"
        apply_result["note"] = "draft PR opened; deploy proceeds via GitHub Actions → Argo after human merge"
        set_status("succeeded", apply_result)
        return apply_result

    set_status("observing", apply_result)
    observe = _observe(spec, plan_row)
    store.add_event(plan_id, "observe", observe)
    apply_result["observe"] = observe
    apply_result["slo_pre"] = pre.to_dict()

    if observe.get("pass"):
        apply_result["status"] = "succeeded"
        details = dict(plan_row.get("details") or {})
        details["policy"] = {"allowed": True, "reason": "executed", "code": "ok"}
        plan_row["details"] = details
        set_status("succeeded", apply_result)
        store.add_event(plan_id, "succeeded", apply_result)
        return apply_result

    if observe.get("verified") is False:
        apply_result["status"] = "failed"
        apply_result["error"] = "remediation outcome unverified; operator review required"
        set_status("failed", apply_result)
        return apply_result

    rb = _dispatch_rollback(plan_row, spec)
    store.add_event(plan_id, "rollback", rb)
    post = snapshot_service(service)
    store.add_event(plan_id, "slo_post_rollback", post.to_dict())
    apply_result["rollback"] = rb
    apply_result["slo_post"] = post.to_dict()
    if not rb.get("attempted") or rb.get("status") == "failed":
        apply_result["status"] = "failed"
        apply_result["error"] = "SLO verification failed and no safe inverse completed"
        set_status("failed", apply_result)
        return apply_result
    apply_result["status"] = "rolled_back"
    set_status("rolled_back", apply_result)
    return apply_result


def _dispatch_execute(plan_row: dict[str, Any], spec) -> dict[str, Any]:
    from remediation.apply.adapters import execute_adapter

    fn = execute_adapter(spec.action)
    if not fn:
        return {"attempted": False, "status": "failed", "error": f"unsupported action {spec.action}"}
    return fn(plan_row)


def _dispatch_rollback(plan_row: dict[str, Any], spec) -> dict[str, Any]:
    action = spec.action
    if action == "rollback_to_last_good":
        return {
            "attempted": False,
            "mode": "no_safe_inverse",
            "status": "failed",
            "reason": "a deployment rollback cannot be automatically reversed safely",
        }
    if action in ("scale_restart_cordon", "restart_data_path"):
        return {
            "attempted": False,
            "mode": "no_safe_inverse",
            "status": "failed",
            "reason": f"{action} has no automatic inverse",
        }
    from remediation.apply.adapters import rollback_adapter

    fn = rollback_adapter(action)
    if fn:
        return fn(plan_row)
    return {"attempted": False, "reason": f"no inverse for {action}"}


def _observe(spec, plan_row: dict[str, Any]) -> dict[str, Any]:
    from remediation.slo import evaluate_gates, snapshot_service

    target = spec.target
    if target.kind == "Rollout" or (plan_row.get("details") or {}).get("use_argo"):
        from remediation.apply.k8s import watch_rollout

        watched = watch_rollout(target.name, target.namespace, timeout_s=min(spec.timeout_seconds, 300))
        if not watched.get("ok"):
            return {"pass": False, "reason": "argo rollout not healthy", "watch": watched}
    # A full post-action window keeps pre-action samples out of the success decision.
    time.sleep(max(spec.slo_gates.window_seconds, int(os.getenv("REMEDIATION_OBSERVE_SLEEP_S", "15"))))
    snap = snapshot_service(spec.candidate_node, window=f"{spec.slo_gates.window_seconds}s")
    return evaluate_gates(
        snap,
        error_rate_max=spec.slo_gates.error_rate_max,
        latency_p99_ms_max=spec.slo_gates.latency_p99_ms_max,
        success_rate_min=spec.slo_gates.success_rate_min,
        require_fresh=True,
    )


def run_once(store=None, worker_id: str | None = None) -> dict[str, Any] | None:
    store = store or _store()
    wid = worker_id or _worker_id()
    plan = store.claim_next(wid)
    if not plan:
        return None
    logger.info("claimed plan %s action=%s", plan.get("id"), plan.get("action"))
    with _lease_heartbeat(store, str(plan.get("id") or ""), wid):
        return execute_plan(plan, store, worker_id=wid)


@contextmanager
def _lease_heartbeat(store, plan_id: str, worker_id: str):
    stop = threading.Event()
    interval = max(5.0, float(os.getenv("REMEDIATION_HEARTBEAT_S", "60")))

    def beat() -> None:
        while not stop.wait(interval):
            try:
                store.heartbeat(plan_id, worker_id)
            except Exception:
                logger.exception("lease heartbeat failed for plan %s", plan_id)
                return

    thread = threading.Thread(
        target=beat,
        name=f"remediation-heartbeat-{plan_id[:8]}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = _store()
    wid = _worker_id()
    interval = float(os.getenv("REMEDIATION_WORKER_POLL_S", "5"))
    logger.info("remediation worker starting id=%s poll=%ss", wid, interval)
    while True:
        try:
            result = run_once(store=store, worker_id=wid)
            if result:
                logger.info("plan finished status=%s", result.get("status"))
        except Exception:  # noqa: BLE001
            logger.exception("worker loop error")
        time.sleep(interval)


if __name__ == "__main__":
    main()
