"""Real Kubernetes apply helpers — exact mutations only, with inverses."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

from remediation.plan_schema import TypedPlanSpec, fingerprint_payload
from remediation.policy import allowlist
from runtime import profile as runtime_profile

logger = logging.getLogger(__name__)

DEFAULT_NS = runtime_profile.remediation_namespace()
FLAGD_CM = runtime_profile.configmap_name()
FLAGD_KEY = runtime_profile.configmap_key()


def _kubectl(args: list[str], *, input_text: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    context = os.getenv("CONTEXT") or os.getenv("KUBECTL_CONTEXT") or ""
    cmd = ["kubectl"]
    plugin = bool(args and args[0] in ("arollouts", "argo"))
    if context and not plugin:
        cmd += ["--context", context]
    cmd += args
    if context and plugin:
        cmd += ["--context", context]
    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def _plan_spec(plan_row: dict[str, Any]) -> TypedPlanSpec | None:
    raw = plan_row.get("plan_spec")
    if not raw and isinstance(plan_row.get("details"), dict):
        raw = (plan_row["details"] or {}).get("plan_spec")
    if not isinstance(raw, dict):
        return None
    try:
        return TypedPlanSpec.from_dict(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("invalid plan_spec: %s", exc)
        return None


def read_flagd_config(namespace: str = DEFAULT_NS) -> dict[str, Any]:
    if not FLAGD_CM or not FLAGD_KEY:
        raise RuntimeError("config patch backend not configured for active profile")
    code, out, err = _kubectl(["get", "cm", FLAGD_CM, "-n", namespace, "-o", "json"])
    if code != 0:
        raise RuntimeError(f"get {FLAGD_CM}: {err or out}")
    obj = json.loads(out)
    data = (obj.get("data") or {}).get(FLAGD_KEY) or "{}"
    doc = json.loads(data) if isinstance(data, str) else data
    return {
        "resource_version": str((obj.get("metadata") or {}).get("resourceVersion") or ""),
        "uid": str((obj.get("metadata") or {}).get("uid") or ""),
        "doc": doc,
        "raw": obj,
    }


def build_flagd_patch_plan(
    *,
    flag_key: str,
    desired_variant: str,
    namespace: str,
    incident_id: str,
    candidate_node: str,
    confidence: float,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from remediation.plan_schema import ExactTarget, PlanChange, SLOGate, TypedPlanSpec
    from remediation.policy import risk_tier_for

    live = read_flagd_config(namespace)
    flags = live["doc"].get("flags") or {}
    if flag_key not in flags:
        raise ValueError(f"flag {flag_key!r} not present in {FLAGD_CM}")
    body = flags[flag_key]
    if not isinstance(body, dict):
        raise ValueError(f"flag {flag_key!r} is not an object")
    variants = body.get("variants") or {}
    if desired_variant not in variants:
        raise ValueError(f"variant {desired_variant!r} not in flag {flag_key}")
    prior = body.get("defaultVariant")
    before_fp = fingerprint_payload(
        {"rv": live["resource_version"], "flag": flag_key, "defaultVariant": prior, "doc": live["doc"]}
    )
    risk, approvals = risk_tier_for("config_patch", confidence)
    spec = TypedPlanSpec(
        action="config_patch",
        fault_class="Config",
        target=ExactTarget(
            cluster=os.getenv("CONTEXT") or os.getenv("KUBECTL_CONTEXT") or "kind-gcp-demo-test",
            namespace=namespace,
            kind="ConfigMap",
            name=FLAGD_CM,
            config_key=f"{FLAGD_KEY}#flags.{flag_key}.defaultVariant",
        ),
        before_fingerprint=before_fp,
        change=PlanChange(
            description=f"Set flag {flag_key} defaultVariant to {desired_variant}",
            patch={
                "flag_key": flag_key,
                "defaultVariant": desired_variant,
                "resource_version": live["resource_version"],
            },
            inverse={
                "flag_key": flag_key,
                "defaultVariant": prior,
                "resource_version": live["resource_version"],
            },
        ),
        risk_tier=risk,
        required_approvals=approvals,
        slo_gates=SLOGate(),
        incident_id=incident_id,
        candidate_node=candidate_node,
        confidence=confidence,
        evidence=evidence or {},
    )
    return {
        "plan_spec": spec.to_dict(),
        "patch": {
            "target": f"configmaps/{FLAGD_CM}",
            "flag_key": flag_key,
            "before": {"defaultVariant": prior, "resourceVersion": live["resource_version"]},
            "after": {"defaultVariant": desired_variant},
            "diff": f"- defaultVariant: {prior}\n+ defaultVariant: {desired_variant}\n",
        },
        "before_fingerprint": before_fp,
    }


def apply_exact_flagd_patch(plan_row: dict[str, Any], *, use_inverse: bool = False) -> dict[str, Any]:
    spec = _plan_spec(plan_row)
    if not spec or spec.action != "config_patch":
        return {"attempted": False, "reason": "missing config_patch plan_spec"}
    change = spec.change.inverse if use_inverse else spec.change.patch
    flag_key = str(change.get("flag_key") or "")
    desired = change.get("defaultVariant")
    expected_rv = str(change.get("resource_version") or spec.change.patch.get("resource_version") or "")
    if not flag_key or desired is None:
        return {"attempted": False, "reason": "plan does not name exact flag_key/defaultVariant"}
    ns = spec.target.namespace or DEFAULT_NS
    live = read_flagd_config(ns)
    if expected_rv and live["resource_version"] != expected_rv and not use_inverse:
        return {
            "attempted": True,
            "mode": "flagd_config_patch",
            "status": "failed",
            "error": (
                f"resourceVersion precondition failed: "
                f"expected {expected_rv}, got {live['resource_version']}"
            ),
        }
    doc = json.loads(json.dumps(live["doc"]))
    flags = doc.setdefault("flags", {})
    if flag_key not in flags or not isinstance(flags[flag_key], dict):
        return {"attempted": True, "mode": "flagd_config_patch", "status": "failed", "error": "flag missing"}
    flags[flag_key]["defaultVariant"] = desired
    patch_body = {
        "data": {FLAGD_KEY: json.dumps(doc, indent=2)},
    }
    code, out, err = _kubectl(
        [
            "patch",
            "cm",
            FLAGD_CM,
            "-n",
            ns,
            "--type",
            "merge",
            "-p",
            json.dumps(patch_body),
        ]
    )
    return {
        "attempted": True,
        "mode": "flagd_config_patch",
        "status": "succeeded" if code == 0 else "failed",
        "flag_key": flag_key,
        "defaultVariant": desired,
        "inverse": use_inverse,
        "stdout": (out or "")[:300],
        "stderr": (err or "")[:500],
        "resource_version_before": live["resource_version"],
    }


def apply_config_patch(plan_row: dict[str, Any]) -> dict[str, Any]:
    return apply_exact_flagd_patch(plan_row, use_inverse=False)


def rollback_config_patch(plan_row: dict[str, Any]) -> dict[str, Any]:
    return apply_exact_flagd_patch(plan_row, use_inverse=True)


def read_workload_revision(name: str, namespace: str = DEFAULT_NS) -> dict[str, Any]:
    code, out, err = _kubectl(
        ["get", "rollout", name, "-n", namespace, "-o", "json"],
    )
    if code == 0 and out:
        obj = json.loads(out)
        return {
            "kind": "Rollout",
            "name": name,
            "namespace": namespace,
            "resource_version": str((obj.get("metadata") or {}).get("resourceVersion") or ""),
            "images": [
                c.get("image")
                for c in ((((obj.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or [])
            ],
            "current_step_index": (obj.get("status") or {}).get("currentStepIndex"),
            "raw_status": obj.get("status") or {},
        }
    code2, out2, err2 = _kubectl(["get", "deploy", name, "-n", namespace, "-o", "json"])
    if code2 != 0:
        raise RuntimeError(f"get workload {name}: {err or err2}")
    obj = json.loads(out2)
    rs_code, rs_out, _ = _kubectl(
        [
            "get",
            "rs",
            "-n",
            namespace,
            "-l",
            f"app.kubernetes.io/name={name}",
            "-o",
            "json",
        ]
    )
    revision = None
    if rs_code == 0 and rs_out:
        items = (json.loads(rs_out).get("items") or [])
        items.sort(
            key=lambda i: int(((i.get("metadata") or {}).get("annotations") or {}).get("deployment.kubernetes.io/revision") or 0),
            reverse=True,
        )
        if len(items) > 1:
            revision = ((items[1].get("metadata") or {}).get("annotations") or {}).get(
                "deployment.kubernetes.io/revision"
            )
        elif items:
            revision = ((items[0].get("metadata") or {}).get("annotations") or {}).get(
                "deployment.kubernetes.io/revision"
            )
    images = [
        c.get("image")
        for c in ((((obj.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or [])
    ]
    return {
        "kind": "Deployment",
        "name": name,
        "namespace": namespace,
        "resource_version": str((obj.get("metadata") or {}).get("resourceVersion") or ""),
        "images": images,
        "revision": revision,
        "replicas": (obj.get("spec") or {}).get("replicas"),
        "resources": ((((obj.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or [{}])[0].get(
            "resources"
        ),
    }


def apply_dependency_rollback(plan_row: dict[str, Any]) -> dict[str, Any]:
    spec = _plan_spec(plan_row)
    node = (spec.candidate_node if spec else None) or str(plan_row.get("candidate_node") or "")
    allowed = allowlist()
    if not node or (allowed and node not in allowed):
        return {"attempted": False, "reason": f"{node or 'missing'} not in allowlist"}
    ns = (spec.target.namespace if spec else None) or DEFAULT_NS
    patch = (spec.change.patch if spec else {}) or {}
    target_revision = patch.get("revision") or (spec.target.revision if spec else None)
    kind = (spec.target.kind if spec else None) or "Deployment"

    if kind == "Rollout" or _rollout_exists(node, ns):
        if target_revision:
            code, out, err = _kubectl(
                ["arollouts", "undo", node, "-n", ns, "--to-revision", str(target_revision)],
            )
            if code != 0:
                code, out, err = _kubectl(["rollout", "undo", f"rollout/{node}", "-n", ns])
        else:
            code, out, err = _kubectl(["arollouts", "abort", node, "-n", ns])
            if code != 0:
                code, out, err = _kubectl(["rollout", "undo", f"rollout/{node}", "-n", ns])
        return {
            "attempted": True,
            "mode": "argo_rollout_undo",
            "status": "succeeded" if code == 0 else "failed",
            "revision": target_revision,
            "stdout": (out or "")[:300],
            "stderr": (err or "")[:500],
        }

    if target_revision:
        code, out, err = _kubectl(
            ["rollout", "undo", f"deploy/{node}", f"--to-revision={target_revision}", "-n", ns]
        )
    else:
        code, out, err = _kubectl(["rollout", "undo", f"deploy/{node}", "-n", ns])
    return {
        "attempted": True,
        "mode": "deployment_rollout_undo",
        "status": "succeeded" if code == 0 else "failed",
        "revision": target_revision,
        "stdout": (out or "")[:300],
        "stderr": (err or "")[:500],
    }


def _rollout_exists(name: str, namespace: str) -> bool:
    code, _, _ = _kubectl(["get", "rollout", name, "-n", namespace])
    return code == 0


def apply_resource_patch(plan_row: dict[str, Any], *, use_inverse: bool = False) -> dict[str, Any]:
    spec = _plan_spec(plan_row)
    if not spec or spec.action != "scale_up":
        return {"attempted": False, "reason": "missing scale_up plan_spec"}
    change = spec.change.inverse if use_inverse else spec.change.patch
    ns = spec.target.namespace
    name = spec.target.name
    replicas = change.get("replicas")
    resources = change.get("resources")
    results: list[dict[str, Any]] = []
    if replicas is not None:
        code, out, err = _kubectl(["scale", f"deploy/{name}", f"--replicas={int(replicas)}", "-n", ns])
        results.append({"op": "scale", "ok": code == 0, "stderr": err[:200]})
    if resources:
        patch = {"spec": {"template": {"spec": {"containers": [{"name": spec.target.container or name, "resources": resources}]}}}}
        code, out, err = _kubectl(
            ["patch", "deploy", name, "-n", ns, "--type", "strategic", "-p", json.dumps(patch)]
        )
        results.append({"op": "resources", "ok": code == 0, "stderr": err[:200]})
    ok = all(r.get("ok") for r in results) if results else False
    return {
        "attempted": True,
        "mode": "resource_patch",
        "status": "succeeded" if ok else "failed",
        "inverse": use_inverse,
        "results": results,
    }


def apply_restart_workload(plan_row: dict[str, Any]) -> dict[str, Any]:
    spec = _plan_spec(plan_row)
    if not spec:
        return {"attempted": False, "reason": "missing plan_spec"}
    ns = spec.target.namespace
    name = spec.target.name
    if _rollout_exists(name, ns):
        ts = datetime.now(timezone.utc).isoformat()
        patch = {
            "spec": {"template": {"metadata": {"annotations": {"causa/restarted-at": ts}}}}
        }
        code, out, err = _kubectl(
            ["patch", "rollout", name, "-n", ns, "--type", "merge", "-p", json.dumps(patch)]
        )
        mode = "rollout_restart"
    else:
        code, out, err = _kubectl(["rollout", "restart", f"deploy/{name}", "-n", ns])
        mode = "deployment_restart"
    return {
        "attempted": True,
        "mode": mode,
        "status": "succeeded" if code == 0 else "failed",
        "stdout": (out or "")[:300],
        "stderr": (err or "")[:500],
    }


def set_rollout_image(name: str, image: str, namespace: str = DEFAULT_NS, container: str | None = None) -> dict[str, Any]:
    cname = container or name
    code, out, err = _kubectl(
        ["arollouts", "set", "image", name, f"{cname}={image}", "-n", namespace],
    )
    if code != 0:
        code, out, err = _kubectl(
            ["set", "image", f"rollout/{name}", f"{cname}={image}", "-n", namespace],
        )
    return {
        "attempted": True,
        "mode": "argo_set_image",
        "status": "succeeded" if code == 0 else "failed",
        "image": image,
        "stderr": (err or "")[:500],
        "stdout": (out or "")[:300],
    }


def watch_rollout(name: str, namespace: str = DEFAULT_NS, timeout_s: int = 300) -> dict[str, Any]:
    code, out, err = _kubectl(
        ["arollouts", "status", name, "-n", namespace, "--timeout", f"{timeout_s}s"],
        timeout=timeout_s + 30,
    )
    if code != 0:
        code2, out2, err2 = _kubectl(
            ["rollout", "status", f"rollout/{name}", "-n", namespace, f"--timeout={timeout_s}s"],
            timeout=timeout_s + 30,
        )
        return {
            "ok": code2 == 0,
            "stdout": (out2 or out or "")[:500],
            "stderr": (err2 or err or "")[:500],
        }
    return {"ok": True, "stdout": (out or "")[:500], "stderr": (err or "")[:500]}
