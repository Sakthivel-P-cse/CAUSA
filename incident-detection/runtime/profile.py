"""Profile-driven runtime config for portable microservice environments."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_PROFILE = "otel-demo"


@dataclass(frozen=True)
class AlertMapping:
    service_label_keys: list[str] = field(default_factory=list)
    namespace_label_keys: list[str] = field(default_factory=list)
    severity_label_keys: list[str] = field(default_factory=list)
    route_label_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    namespaces: list[str]
    remediation_namespace: str
    service_allowlist: list[str]
    auto_actions: list[str]
    monitored_services_regex: str
    excluded_services_regex: str
    internal_namespaces: list[str]
    configmap_name: str
    configmap_key: str
    remediation_base_branch: str
    min_confidence: float
    alert_mapping: AlertMapping
    capabilities: dict[str, list[str]]
    rollout_provider: dict[str, str]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProfileConfig":
        alert = raw.get("alert_mapping") or {}
        return cls(
            name=str(raw.get("name") or DEFAULT_PROFILE),
            namespaces=[str(x) for x in (raw.get("namespaces") or ["otel-demo"])],
            remediation_namespace=str(raw.get("remediation_namespace") or "otel-demo"),
            service_allowlist=[str(x) for x in (raw.get("service_allowlist") or [])],
            auto_actions=[str(x) for x in (raw.get("auto_actions") or [])],
            monitored_services_regex=str(raw.get("monitored_services_regex") or ""),
            excluded_services_regex=str(raw.get("excluded_services_regex") or ""),
            internal_namespaces=[str(x) for x in (raw.get("internal_namespaces") or [])],
            configmap_name=str(raw.get("configmap_name") or "flagd-config"),
            configmap_key=str(raw.get("configmap_key") or "demo.flagd.json"),
            remediation_base_branch=str(raw.get("remediation_base_branch") or "master"),
            min_confidence=float(raw.get("min_confidence") or 0.15),
            alert_mapping=AlertMapping(
                service_label_keys=[str(x) for x in (alert.get("service_label_keys") or ["service"])],
                namespace_label_keys=[str(x) for x in (alert.get("namespace_label_keys") or ["namespace"])],
                severity_label_keys=[str(x) for x in (alert.get("severity_label_keys") or ["severity"])],
                route_label_keys=[str(x) for x in (alert.get("route_label_keys") or ["route"])],
            ),
            capabilities={str(k): [str(a) for a in (v or [])] for k, v in (raw.get("capabilities") or {}).items()},
            rollout_provider={str(k): str(v) for k, v in (raw.get("rollout_provider") or {}).items()},
        )


def _profiles_root() -> Path:
    return Path(__file__).resolve().parents[1] / "profiles"


def _default_profile_dict() -> dict[str, Any]:
    return {
        "name": "otel-demo",
        "namespaces": ["otel-demo"],
        "remediation_namespace": "otel-demo",
        "service_allowlist": [
            "flagd",
            "product-catalog",
            "payment",
            "cart",
            "recommendation",
            "ad",
            "email",
            "checkout",
            "currency",
            "shipping",
            "fraud-detection",
            "frontend",
            "frontend-proxy",
            "valkey-cart",
        ],
        "auto_actions": [],
        "monitored_services_regex": "",
        "excluded_services_regex": "",
        "internal_namespaces": [
            "alertmanager",
            "kube-system",
            "kube-public",
            "kube-node-lease",
            "kube-state-metrics",
            "local-path-storage",
            "loki",
            "mimir",
            "minio-loki",
            "minio-mimir",
            "minio-tempo",
            "node-exporter",
            "otel-agent",
            "otel-collector",
            "otel-lb",
            "postgres",
            "prometheus",
            "stage2-rca",
            "causa-dashboard",
            "causa-ingest",
        ],
        "configmap_name": "flagd-config",
        "configmap_key": "demo.flagd.json",
        "remediation_base_branch": "master",
        "min_confidence": 0.15,
        "alert_mapping": {
            "service_label_keys": [
                "service",
                "app",
                "app_kubernetes_io_name",
                "k8s_app",
                "workload",
                "deployment",
                "container",
            ],
            "namespace_label_keys": ["namespace"],
            "severity_label_keys": ["severity"],
            "route_label_keys": ["route"],
        },
        "capabilities": {
            "*": [
                "config_patch",
                "scale_up",
                "rollback_to_last_good",
                "code_fix_pr",
                "restart_data_path",
                "scale_restart_cordon",
            ],
            "flagd": ["config_patch"],
            "payment": ["code_fix_pr", "scale_up", "rollback_to_last_good"],
            "cart": ["code_fix_pr", "scale_up", "rollback_to_last_good"],
            "checkout": ["code_fix_pr", "scale_up", "rollback_to_last_good"],
            "recommendation": ["code_fix_pr", "scale_up", "rollback_to_last_good"],
            "product-catalog": ["code_fix_pr", "scale_up", "rollback_to_last_good"],
            "frontend": ["code_fix_pr", "scale_up", "rollback_to_last_good"],
            "frontend-proxy": ["code_fix_pr", "scale_up", "rollback_to_last_good"],
            "valkey-cart": ["scale_up"],
        },
        "rollout_provider": {"recommendation": "argo"},
    }


def _deep_update(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if k in {"capabilities", "rollout_provider", "alert_mapping"} and isinstance(v, dict):
            out[k] = dict(v)
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_update(out[k], v)
        else:
            out[k] = v
    return out


def _env_list(name: str) -> list[str] | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return [x.strip() for x in raw.split(",") if x.strip()]


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    if ns := _env_list("REMEDIATION_ALLOWED_NAMESPACES"):
        out["namespaces"] = ns
    if env_ns := os.getenv("REMEDIATION_NAMESPACE", "").strip():
        out["remediation_namespace"] = env_ns
    if allow := _env_list("REMEDIATION_SERVICE_ALLOWLIST"):
        out["service_allowlist"] = allow
    if auto := _env_list("CAUSA_REMEDIATION_AUTO_ACTIONS"):
        out["auto_actions"] = auto
    if mon := os.getenv("CAUSA_MONITORED_SERVICES_REGEX", "").strip():
        out["monitored_services_regex"] = mon
    if exc := os.getenv("CAUSA_EXCLUDED_SERVICES_REGEX", "").strip():
        out["excluded_services_regex"] = exc
    if cm := os.getenv("REMEDIATION_CONFIGMAP_NAME", "").strip():
        out["configmap_name"] = cm
    if key := os.getenv("REMEDIATION_CONFIGMAP_KEY", "").strip():
        out["configmap_key"] = key
    if base := os.getenv("CAUSA_REMEDIATION_BASE", "").strip():
        out["remediation_base_branch"] = base
    if floor := os.getenv("REMEDIATION_MIN_CONFIDENCE", "").strip():
        try:
            out["min_confidence"] = float(floor)
        except ValueError:
            pass
    return out


@lru_cache(maxsize=4)
def load_profile(profile_name: str | None = None) -> ProfileConfig:
    name = (profile_name or os.getenv("CAUSA_PROFILE", DEFAULT_PROFILE) or DEFAULT_PROFILE).strip() or DEFAULT_PROFILE
    base = _default_profile_dict()
    p = _profiles_root() / f"{name}.json"
    if p.is_file():
        try:
            file_data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(file_data, dict):
                base = _deep_update(base, file_data)
        except Exception:
            pass
    base = _apply_env_overrides(base)
    base["name"] = name
    return ProfileConfig.from_dict(base)


def remediation_namespace() -> str:
    return load_profile().remediation_namespace


def allowed_namespaces() -> set[str]:
    return set(load_profile().namespaces)


def service_allowlist() -> set[str]:
    return set(load_profile().service_allowlist)


def action_auto_allowlist() -> set[str]:
    return set(load_profile().auto_actions)


def base_branch() -> str:
    return load_profile().remediation_base_branch


def min_confidence_floor() -> float:
    return float(load_profile().min_confidence)


def configmap_name() -> str:
    return load_profile().configmap_name


def configmap_key() -> str:
    return load_profile().configmap_key


def supports_action(service: str, action: str) -> bool:
    prof = load_profile()
    actions = set(
        prof.capabilities[service]
        if service in prof.capabilities
        else prof.capabilities.get("*", [])
    )
    return action in actions


def rollout_provider(service: str) -> str:
    return load_profile().rollout_provider.get(service, "deployment")
