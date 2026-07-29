from __future__ import annotations

from typing import Any

_PLATFORM_LAYERS = frozenset({"platform", "slo"})
_EXCLUDED_ALERT_PREFIXES = (
    "minio",
    "loki",
    "tempo",
    "mimir",
    "otel",
    "prometheus",
    "alertmanager",
    "persistentvolume",
    "hpa",
    "slo",
)
_EXCLUDED_SERVICES = frozenset({
    "minio",
    "loki",
    "tempo",
    "mimir",
    "prometheus",
    "alertmanager",
    "otel-collector",
    "otel-agent",
})


def is_app_scope_incident(item: dict[str, Any]) -> bool:
    layer = str(item.get("layer") or "").strip().lower()
    if layer in _PLATFORM_LAYERS:
        return False
    alert = str(item.get("alert_name") or "").strip().lower()
    if any(alert.startswith(p) for p in _EXCLUDED_ALERT_PREFIXES):
        return False
    svc = str(
        item.get("alerted_service") or item.get("root_cause_service") or ""
    ).strip().lower()
    if svc in _EXCLUDED_SERVICES:
        return False
    return True
