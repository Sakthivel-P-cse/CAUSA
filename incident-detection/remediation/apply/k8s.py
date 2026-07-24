"""Real kubectl apply helpers for Config / Dependency remediations."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_NS = os.getenv("REMEDIATION_NAMESPACE", "otel-demo")
ALLOWLIST = {
    x.strip()
    for x in os.getenv(
        "REMEDIATION_SERVICE_ALLOWLIST",
        "flagd,product-catalog,payment,cart,recommendation,ad,email,checkout,currency,shipping,fraud-detection",
    ).split(",")
    if x.strip()
}


def _kubectl(args: list[str], *, input_text: str | None = None) -> tuple[int, str, str]:
    context = os.getenv("CONTEXT") or os.getenv("KUBECTL_CONTEXT") or ""
    cmd = ["kubectl"]
    if context:
        cmd += ["--context", context]
    cmd += args
    try:
        proc = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def apply_config_patch(plan_row: dict[str, Any]) -> dict[str, Any]:
    node = str(plan_row.get("candidate_node") or "")
    if node and node not in ALLOWLIST and node != "flagd":
        return {"attempted": False, "reason": f"{node} not in allowlist"}

    ns = DEFAULT_NS
    code, out, err = _kubectl(
        ["get", "cm", "flagd-config", "-n", ns, "-o", "jsonpath={.data.demo\\.flagd\\.json}"]
    )
    if code != 0 or not out:
        target = node if node in ALLOWLIST else "flagd"
        code2, _, err2 = _kubectl(["rollout", "restart", f"deploy/{target}", "-n", ns])
        return {
            "attempted": True,
            "mode": "kubectl_restart",
            "status": "succeeded" if code2 == 0 else "failed",
            "stderr": (err or err2)[:500],
        }
    try:
        doc = json.loads(out)
        for _name, body in (doc.get("flags") or {}).items():
            if isinstance(body, dict) and "defaultVariant" in body:
                variants = body.get("variants") or {}
                if "off" in variants:
                    body["defaultVariant"] = "off"
        payload = json.dumps({"data": {"demo.flagd.json": json.dumps(doc, indent=2)}})
        code3, _, err3 = _kubectl(
            ["patch", "cm", "flagd-config", "-n", ns, "--type", "merge", "-p", payload]
        )
        return {
            "attempted": True,
            "mode": "flagd_config_patch",
            "status": "succeeded" if code3 == 0 else "failed",
            "stderr": err3[:500] if err3 else "",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "attempted": True,
            "mode": "flagd_config_patch",
            "status": "failed",
            "error": str(exc),
        }


def apply_dependency_rollback(plan_row: dict[str, Any]) -> dict[str, Any]:
    node = str(plan_row.get("candidate_node") or "")
    if not node or node not in ALLOWLIST:
        return {"attempted": False, "reason": f"{node or 'missing'} not in allowlist"}
    ns = DEFAULT_NS
    code, out, err = _kubectl(
        ["get", "deploy", node, "-n", ns, "-o", "jsonpath={.spec.replicas}"]
    )
    replicas = 1
    if code == 0 and out.strip().isdigit():
        replicas = max(1, int(out.strip()))
    code2, _, err2 = _kubectl(["scale", f"deploy/{node}", f"--replicas={replicas}", "-n", ns])
    code3, _, err3 = _kubectl(["rollout", "restart", f"deploy/{node}", "-n", ns])
    ok = code2 == 0 or code3 == 0
    return {
        "attempted": True,
        "mode": "dependency_rollback",
        "status": "succeeded" if ok else "failed",
        "replicas": replicas,
        "stderr": (err + err2 + err3)[:500],
    }
