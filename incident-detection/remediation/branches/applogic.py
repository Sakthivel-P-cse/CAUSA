"""AppLogic deep branch — real source path + concrete suggested fix."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from contracts import RankedResult, RootCauseCandidate
from remediation.plan_schema import ExactTarget, PlanChange, SLOGate, TypedPlanSpec, fingerprint_payload
from remediation.policy import risk_tier_for
from runtime import profile as runtime_profile

# Prefer Causa demo-bugs overlays, then OpenTelemetry demo tree.
SERVICE_SOURCE_MAP = {
    "payment": [
        ("demo-bugs/payment/charge.js", "fix TypeError on amount.nano — guard optional nano"),
        ("src/payment/charge.js", "guard amount.units / amount.nano before use"),
    ],
    "cart": [
        ("demo-bugs/cart/CartService.cs", "fix NullReferenceException on empty cart"),
        ("src/cart/src/services/CartService.cs", "null-check cart items before mutate"),
        ("services/cart/handler.py", "normalize cart user id before uppercase"),
    ],
    "checkout": [
        ("demo-bugs/checkout/main.go", "fix divide-by-zero in PlaceOrder"),
        ("src/checkout/main.go", "guard zero quantity / money divide"),
    ],
    "product-catalog": [
        ("demo-bugs/product-catalog/main.go", "fix nil Money panic"),
        ("src/product-catalog/main.go", "nil-check Money before format"),
    ],
    "recommendation": [
        ("demo-bugs/recommendation/recommendation_server.py", "fix IndexError off-by-one"),
        ("src/recommendation/recommendation_server.py", "clamp index into product list"),
    ],
    "frontend": [("src/frontend/", "frontend error boundary / API client timeout")],
    "frontend-proxy": [("src/frontend-proxy/", "proxy upstream timeout / retry budget")],
}


def _search_roots() -> list[Path]:
    roots: list[Path] = []
    env = os.getenv("CAUSA_REMEDIATION_REPO", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_dir():
            return [p]
    causa = Path(__file__).resolve().parents[3]
    if (causa / "demo-bugs").is_dir():
        roots.append(causa)
    demo = Path("/home/bhuvi/PROJECTS/opentelemetry-demo")
    if demo.is_dir():
        roots.append(demo)
    return roots or [causa]


def _resolve_source(node: str) -> tuple[Path | None, str, str]:
    entries = SERVICE_SOURCE_MAP.get(node) or [(f"src/{node}/", f"inspect {node} service")]
    for root in _search_roots():
        for rel, hint in entries:
            path = root / rel
            if path.is_file():
                return path, rel, hint
            if path.is_dir():
                return path, rel, hint
    rel, hint = entries[0]
    return None, rel, hint


def _suggested_diff(path: Path | None, rel: str, node: str, hint: str, explanation: str) -> str:
    if path and path.is_file():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        excerpt = "\n".join(lines[:80])
        return (
            f"--- a/{rel}\n+++ b/{rel}\n"
            f"@@ suggested fix for {node}: {hint} @@\n"
            f"# Evidence: {(explanation or '')[:240]}\n"
            f"# Current source excerpt ({path}):\n"
            + "\n".join(f" {ln}" for ln in excerpt.splitlines()[:40])
        )
    return (
        f"--- a/{rel}\n+++ b/{rel}\n"
        f"@@\n"
        f"- # buggy path in {node}\n"
        f"+ # {hint}\n"
        f"+ # Evidence: {(explanation or '')[:240]}\n"
    )


def _patch_ops(node: str, rel: str) -> list[dict[str, str]]:
    fixes = {
        "payment": [
            {
                "path": "demo-bugs/payment/charge.js",
                "search": "const feeNanos = Math.floor(Number(request.amount.nano) / units);",
                "replace": "const feeNanos = Math.floor(Number(request.amount.nanos ?? 0) / Math.max(1, Number(units || 0)));",
            }
        ],
        "cart": [
            {
                "path": "demo-bugs/cart/CartService.cs",
                "search": "CartItem ghost = null;\n            activity?.SetTag(\"demo.cart.first_product\", ghost.ProductId);",
                "replace": "var firstProduct = cart.Items.Count > 0 ? cart.Items[0].ProductId : \"empty\";\n            activity?.SetTag(\"demo.cart.first_product\", firstProduct);",
            },
            {
                "path": "services/cart/handler.py",
                "search": "    return {\"user\": user_id.upper(), \"items\": []}",
                "replace": "    return {\"user\": str(user_id).strip().upper(), \"items\": []}",
            },
        ],
        "checkout": [
            {
                "path": "demo-bugs/checkout/main.go",
                "search": "\t\tqty := it.GetItem().GetQuantity()\n\t\t_ = it.Cost.GetUnits() / int64(qty-qty)",
                "replace": "\t\tqty := it.GetItem().GetQuantity()\n\t\tden := int64(qty)\n\t\tif den < 1 {\n\t\t\tden = 1\n\t\t}\n\t\t_ = it.Cost.GetUnits() / den",
            }
        ],
        "product-catalog": [
            {
                "path": "demo-bugs/product-catalog/main.go",
                "search": "\tvar price *pb.Money\n\tspan.SetAttributes(\n\t\tattribute.Int64(\"demo.product.price.units\", price.Units),\n\t)",
                "replace": "\tprice := found.GetPriceUsd()\n\tif price != nil {\n\t\tspan.SetAttributes(attribute.Int64(\"demo.product.price.units\", price.Units))\n\t}",
            }
        ],
        "recommendation": [
            {
                "path": "demo-bugs/recommendation/recommendation_server.py",
                "search": "        if num_products > 0:\n            _ = filtered_products[num_products]",
                "replace": "        if num_products > 0:\n            _ = filtered_products[num_products - 1]",
            }
        ],
    }
    all_ops = fixes.get(node, [])
    filtered = [op for op in all_ops if str(op.get("path") or "") == rel]
    return filtered


def propose_code_fix_pr(
    candidate: RootCauseCandidate,
    ranked: RankedResult | None = None,
    *,
    incident_id: str = "",
    confidence: float | None = None,
    nim_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conf = float(confidence if confidence is not None else candidate.confidence or 0.0)
    path, rel, hint = _resolve_source(candidate.node_id)
    existing = ""
    if path and path.is_file():
        try:
            existing = path.read_text(encoding="utf-8", errors="replace")[:6000]
        except OSError:
            existing = ""

    diff = _suggested_diff(path, rel, candidate.node_id, hint, candidate.explanation or "")
    if isinstance(nim_patch, dict) and nim_patch.get("diff"):
        diff = str(nim_patch["diff"])
        hint = str(nim_patch.get("title") or hint)

    title = (
        str(nim_patch.get("title"))
        if isinstance(nim_patch, dict) and nim_patch.get("title")
        else f"fix({candidate.node_id}): {hint}"
    )
    body = "\n".join(
        [
            "## Root cause",
            candidate.explanation or "(no explanation)",
            "",
            f"- service: `{candidate.node_id}`",
            f"- confidence: {candidate.confidence:.3f}",
            f"- score: {candidate.score:.3f}",
            "",
            f"## Fix target `{rel}`",
            hint,
            "",
            "```diff",
            diff[:3500],
            "```",
            "",
            "Draft PR only — never auto-merged. CD: GitHub Actions → Argo canary.",
        ]
    )
    risk, approvals = risk_tier_for("code_fix_pr", conf)
    before_fp = fingerprint_payload({"path": rel, "snippet": existing[:800], "node": candidate.node_id})
    patch = {
        "repo": str(_search_roots()[0]),
        "path": rel,
        "title": title,
        "body": body,
        "base": runtime_profile.base_branch(),
        "draft": True,
        "merged": False,
        "suggested_diff": diff,
        "fix_summary": hint,
        "source_excerpt": existing[:2000],
        "file_ops": _patch_ops(candidate.node_id, rel),
    }
    spec = TypedPlanSpec(
        action="code_fix_pr",
        fault_class="AppLogic",
        target=ExactTarget(
            cluster=os.getenv("CONTEXT") or "kind-gcp-demo-test",
            namespace=runtime_profile.remediation_namespace(),
            kind="GitHubPullRequest",
            name=candidate.node_id,
            config_key=rel,
        ),
        before_fingerprint=before_fp,
        change=PlanChange(
            description=hint,
            patch=patch,
            inverse={"action": "close_pr", "note": "close draft if abandoned"},
        ),
        risk_tier=risk,
        required_approvals=approvals,
        slo_gates=SLOGate(require_fresh=False),
        incident_id=incident_id,
        candidate_node=candidate.node_id,
        confidence=conf,
        evidence=candidate.to_dict(),
    )
    return {
        "plan_spec": spec.to_dict(),
        "before_fingerprint": before_fp,
        "solution": {
            "summary": hint,
            "path": rel,
            "diff": diff,
            "excerpt": existing[:2000],
        },
        "pr": {
            "title": title,
            "body": body,
            "base": patch["base"],
            "head": f"causa/fix-{candidate.node_id}",
            "merged": False,
            "draft": True,
            "path": rel,
            "diff": diff,
        },
        "evidence_trail": candidate.to_dict(),
    }
