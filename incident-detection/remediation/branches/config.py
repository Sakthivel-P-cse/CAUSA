"""Config deep branch — exact flagd flag patch from live cluster state."""

from __future__ import annotations

import logging
import os
from typing import Any

from contracts import RootCauseCandidate
from runtime import profile as runtime_profile

logger = logging.getLogger(__name__)

DEFAULT_NS = runtime_profile.remediation_namespace()


def _infer_flag_key(candidate: RootCauseCandidate) -> str | None:
    flags = candidate.flags or {}
    for key in ("flag_key", "feature_flag", "flag"):
        val = flags.get(key)
        if val:
            return str(val)
    for key, val in (candidate.dev_vector or {}).items():
        if "flag" in str(key).lower() and val:
            return str(val)
    explanation = (candidate.explanation or "").lower()
    for token in explanation.replace(",", " ").split():
        if token.startswith("productcatalog") or token.endswith("failure") or "flag" in token:
            if "." in token or "failure" in token:
                return token.strip(".:;")
    return None


def propose_config_patch(candidate: RootCauseCandidate, *, incident_id: str = "", confidence: float | None = None) -> dict[str, Any]:
    ns = DEFAULT_NS
    flag_key = _infer_flag_key(candidate)
    conf = float(confidence if confidence is not None else candidate.confidence or 0.0)
    try:
        from remediation.apply.k8s import build_flagd_patch_plan, read_flagd_config

        live = read_flagd_config(ns)
        flags = (live.get("doc") or {}).get("flags") or {}
        if not flag_key:
            for name, body in flags.items():
                if not isinstance(body, dict):
                    continue
                variants = body.get("variants") or {}
                if "off" in variants and body.get("defaultVariant") != "off":
                    flag_key = name
                    break
        if not flag_key or flag_key not in flags:
            raise ValueError(f"could not resolve flag key for {candidate.node_id}")
        body = flags[flag_key]
        variants = body.get("variants") or {}
        desired = "off" if "off" in variants else next(iter(variants), None)
        if desired is None:
            raise ValueError(f"flag {flag_key} has no variants")
        built = build_flagd_patch_plan(
            flag_key=flag_key,
            desired_variant=str(desired),
            namespace=ns,
            incident_id=incident_id or "",
            candidate_node=candidate.node_id,
            confidence=conf,
            evidence=candidate.to_dict(),
        )
        return {
            **built,
            "evidence_trail": candidate.to_dict(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("live flagd propose failed: %s", exc)
        return {
            "error": str(exc),
            "executable": False,
            "evidence_trail": candidate.to_dict(),
            "summary": f"Cannot build exact config plan: {exc}",
        }
