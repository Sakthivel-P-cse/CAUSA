"""NVIDIA NIM (OpenAI-compatible) client for Dev C classify + remediation text."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import urllib.error
import urllib.request

from contracts import CLASSIFICATION_TARGETS, FaultClass

logger = logging.getLogger(__name__)

_NIM_CONFIDENCE_FLOOR = 0.55


@dataclass
class NimClassifyResult:
    fault_class: FaultClass | None
    confidence: float
    rationale: str
    remediation: str
    raw: dict[str, Any]
    ok: bool
    error: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fault_class": self.fault_class.value if self.fault_class else None,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "remediation": self.remediation,
            "ok": self.ok,
            "error": self.error,
            "model": self.model,
            "raw": self.raw,
        }


def nim_enabled() -> bool:
    if os.getenv("CAUSA_NIM_ENABLED", "1").strip() in ("0", "false", "False", "no"):
        return False
    return bool(os.getenv("NVIDIA_API_KEY", "").strip())


def nim_config() -> dict[str, str]:
    return {
        "api_key": os.getenv("NVIDIA_API_KEY", "").strip(),
        "base_url": os.getenv(
            "CAUSA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"
        ).rstrip("/"),
        "model": os.getenv("CAUSA_NIM_MODEL", "z-ai/glm-5.2"),
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _parse_fault_class(value: Any) -> FaultClass | None:
    if value is None:
        return None
    raw = str(value).strip()
    for fc in CLASSIFICATION_TARGETS:
        if fc.value.lower() == raw.lower() or fc.name.lower() == raw.lower():
            return fc
    aliases = {
        "app_logic": FaultClass.APP_LOGIC,
        "applogic": FaultClass.APP_LOGIC,
        "app-logic": FaultClass.APP_LOGIC,
    }
    return aliases.get(raw.lower().replace(" ", ""))


def build_classify_prompt(context: dict[str, Any]) -> list[dict[str, str]]:
    allowed = ", ".join(fc.value for fc in CLASSIFICATION_TARGETS)
    payload = json.dumps(context, indent=2, default=str)
    system = (
        "You are Causa Dev C, an SRE fault classifier. "
        "Respond with a single JSON object only (no markdown prose outside JSON) with keys: "
        "fault_class, confidence (0-1), rationale (short operator English), "
        "remediation (short operator English recommended action). "
        f"fault_class must be one of: {allowed}."
    )
    user = (
        "Classify this RCA incident and propose remediation guidance.\n\n"
        f"{payload}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_applogic_patch_prompt(context: dict[str, Any]) -> list[dict[str, str]]:
    payload = json.dumps(context, indent=2, default=str)
    system = (
        "You are Causa Dev C generating a draft code-fix proposal. "
        "Respond with JSON only: {\"title\": str, \"body\": str, \"diff\": str, "
        "\"files\": [str]}. The diff is illustrative and will be human-reviewed; never claim it was merged."
    )
    user = f"Propose a draft fix PR payload for this AppLogic incident:\n\n{payload}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def chat_completions(
    messages: list[dict[str, str]],
    *,
    timeout_s: float | None = None,
) -> tuple[str | None, str | None, str]:
    """Returns (content, error, model). Never logs the API key."""
    cfg = nim_config()
    if not cfg["api_key"]:
        return None, "NVIDIA_API_KEY missing", cfg["model"]
    timeout = timeout_s
    if timeout is None:
        timeout = float(os.getenv("CAUSA_NIM_TIMEOUT_S", "180"))
    url = f"{cfg['base_url']}/chat/completions"
    body = json.dumps(
        {
            "model": cfg["model"],
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1200,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        logger.warning("NIM HTTP %s: %s", exc.code, detail)
        return None, f"NIM HTTP {exc.code}", cfg["model"]
    except Exception as exc:  # noqa: BLE001 — network boundary
        logger.warning("NIM request failed: %s", type(exc).__name__)
        return None, f"NIM request failed: {type(exc).__name__}", cfg["model"]

    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None, "NIM response missing content", cfg["model"]
    return str(content), None, cfg["model"]


def classify_with_nim(context: dict[str, Any]) -> NimClassifyResult:
    if not nim_enabled():
        return NimClassifyResult(
            fault_class=None,
            confidence=0.0,
            rationale="",
            remediation="",
            raw={},
            ok=False,
            error="NIM disabled or NVIDIA_API_KEY missing",
            model=nim_config()["model"],
        )
    content, err, model = chat_completions(build_classify_prompt(context))
    if err or not content:
        return NimClassifyResult(
            fault_class=None,
            confidence=0.0,
            rationale="",
            remediation="",
            raw={},
            ok=False,
            error=err or "empty content",
            model=model,
        )
    parsed = _extract_json(content) or {}
    fc = _parse_fault_class(parsed.get("fault_class"))
    try:
        conf = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return NimClassifyResult(
        fault_class=fc,
        confidence=max(0.0, min(1.0, conf)),
        rationale=str(parsed.get("rationale") or "").strip(),
        remediation=str(parsed.get("remediation") or "").strip(),
        raw=parsed,
        ok=fc is not None,
        error=None if fc is not None else "could not parse fault_class",
        model=model,
    )


def propose_applogic_patch_with_nim(context: dict[str, Any]) -> dict[str, Any] | None:
    if not nim_enabled():
        return None
    content, err, model = chat_completions(build_applogic_patch_prompt(context))
    if err or not content:
        return None
    parsed = _extract_json(content)
    if not parsed:
        return None
    parsed["model"] = model
    return parsed


def merge_fault_class(
    *,
    nim: NimClassifyResult | None,
    rules_fault_class: FaultClass,
    rca_failure_type: str | None = None,
) -> tuple[FaultClass, str]:
    """Return (chosen_class, source) where source is nim|rules|rca."""
    if rca_failure_type:
        parsed = _parse_fault_class(rca_failure_type)
        if parsed is not None:
            # Prefer explicit structural RCA class when present, unless NIM is strong.
            if (
                nim
                and nim.ok
                and nim.fault_class is not None
                and nim.confidence >= _NIM_CONFIDENCE_FLOOR
                and nim.fault_class != parsed
                and nim.confidence >= 0.75
            ):
                return nim.fault_class, "nim"
            return parsed, "rca"
    if (
        nim
        and nim.ok
        and nim.fault_class is not None
        and nim.confidence >= _NIM_CONFIDENCE_FLOOR
    ):
        return nim.fault_class, "nim"
    return rules_fault_class, "rules"


def nim_health() -> dict[str, Any]:
    cfg = nim_config()
    return {
        "enabled": nim_enabled(),
        "key_present": bool(cfg["api_key"]),
        "base_url": cfg["base_url"],
        "model": cfg["model"],
    }
