"""
service_scope.py — Scope helpers: which services are "application" services.

Stage-1 monitors user/application microservices, not the internal Causa
observability stack (Tempo/Loki/Prometheus/OTel/etc). These helpers decide
what belongs in scope and normalize pod names → stable service names.

Self-contained: env-configurable, no dependency on any other module.

Env knobs:
  CAUSA_MONITORED_SERVICES_REGEX   allowlist (if set, only matches are in scope)
  CAUSA_EXCLUDED_SERVICES_REGEX    extra excludes, ORed with the defaults
  CAUSA_SERVICE_NAME_LABEL_KEYS    ordered label keys used to read a service name
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

_INTERNAL_NAMESPACES = {
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
}

# Defaults target Causa internals so app/test microservices remain visible.
_DEFAULT_EXCLUDED_SERVICES_RE = (
    r"^(?:"
    r"alertmanager|"
    r"coredns(?:$|-.*)|"
    r"etcd(?:$|-.*)|"
    r"grafana|"
    r"kindnet(?:$|-.*)|"
    r"kube(?:$|-.*)|"
    r"kube-(?:state-metrics|proxy|dns|scheduler|apiserver|controller-manager).*|"
    r"loki|"
    r"local-path-provisioner(?:$|-.*)|"
    # Synthetic traffic driver, not a service under test. otel-demo names it
    # `load-generator`; the held-out Boutique app uses `loadgenerator`.
    r"load-generator(?:$|-.*)|"
    r"loadgenerator(?:$|-.*)|"
    r"mimir|"
    r"minio-(?:loki|mimir|tempo)(?:$|-.*)|"
    r"node-exporter|"
    r"otel-(?:agent|collector|lb)(?:$|-.*)|"
    r"postgres|"
    r"prometheus|"
    r"rca-engine|"
    # NOTE: the cart cache (otel-demo `valkey-cart`, Boutique `redis-cart`) is an
    # APPLICATION dependency and S1's true root — it must stay IN scope, so no
    # redis/valkey exclusion here.
    r"stage2-rca|"
    r"tempo|"
    r"causa-dashboard|"
    r"causa-ingest"
    r")$"
)

_REPLICASET_POD_RE = re.compile(r"^(?P<base>.+)-[a-f0-9]{8,10}-[a-z0-9]{5}$", re.IGNORECASE)
_STATEFULSET_POD_RE = re.compile(r"^(?P<base>.+)-\d+$")
_DAEMONSET_POD_RE = re.compile(r"^(?P<base>.+)-[a-z0-9]{5}$", re.IGNORECASE)


@lru_cache(maxsize=1)
def _monitored_services_re() -> re.Pattern[str] | None:
    raw = (os.getenv("CAUSA_MONITORED_SERVICES_REGEX") or "").strip()
    if not raw:
        return None
    try:
        return re.compile(raw, re.IGNORECASE)
    except re.error:
        return None


@lru_cache(maxsize=1)
def _excluded_services_re() -> re.Pattern[str] | None:
    extra = (os.getenv("CAUSA_EXCLUDED_SERVICES_REGEX") or "").strip()
    pattern = (
        _DEFAULT_EXCLUDED_SERVICES_RE
        if not extra
        else f"(?:{_DEFAULT_EXCLUDED_SERVICES_RE})|(?:{extra})"
    )
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(_DEFAULT_EXCLUDED_SERVICES_RE, re.IGNORECASE)


def _service_name_label_keys() -> list[str]:
    keys_env = os.getenv(
        "CAUSA_SERVICE_NAME_LABEL_KEYS",
        "service,app,app_kubernetes_io_name,k8s_app,workload,deployment,container",
    )
    return [k.strip() for k in keys_env.split(",") if k.strip()]


def monitored_services_pattern() -> str | None:
    raw = (os.getenv("CAUSA_MONITORED_SERVICES_REGEX") or "").strip()
    return raw or None


def excluded_services_pattern() -> str:
    extra = (os.getenv("CAUSA_EXCLUDED_SERVICES_REGEX") or "").strip()
    return (
        _DEFAULT_EXCLUDED_SERVICES_RE
        if not extra
        else f"(?:{_DEFAULT_EXCLUDED_SERVICES_RE})|(?:{extra})"
    )


def extract_service_from_labels(labels: dict[str, str]) -> str | None:
    """Best-effort service extraction from Alertmanager / stream labels."""
    for key in _service_name_label_keys():
        if val := labels.get(key):
            return val

    # Fallbacks if none of the above match
    if server := labels.get("server"):
        return server
    if pod := labels.get("pod"):
        parts = pod.rsplit("-", 2)
        return parts[0] if len(parts) >= 3 else pod
    if inst := labels.get("instance"):
        return inst
    return labels.get("namespace")


def infer_service_name_from_pod(pod_name: str | None) -> str | None:
    """Infer a stable service/controller name from a Kubernetes pod name."""
    if not pod_name:
        return None

    value = pod_name.strip()
    if not value:
        return None

    for pattern in (_REPLICASET_POD_RE, _STATEFULSET_POD_RE, _DAEMONSET_POD_RE):
        match = pattern.match(value)
        if match:
            base = match.group("base").strip()
            if base:
                return base

    return value


def is_internal_namespace(namespace: str | None) -> bool:
    if not namespace:
        return False
    return namespace.strip().lower() in _INTERNAL_NAMESPACES


def is_monitored_service(service: str | None) -> bool:
    """
    True when a service should be included in monitoring/RCA scope.

    Allowlist (CAUSA_MONITORED_SERVICES_REGEX) wins if set; otherwise
    everything is in scope except the internal-stack excludes.
    """
    if not service:
        return False

    value = service.strip()
    if not value or value.lower() in {"unknown", "none", "n/a"}:
        return False

    allow_re = _monitored_services_re()
    if allow_re is not None:
        return bool(allow_re.search(value))

    exclude_re = _excluded_services_re()
    return not bool(exclude_re and exclude_re.search(value))


def is_monitored_alert(labels: dict[str, str], resolved_service: str | None = None) -> bool:
    """True if an alert belongs to external/app service scope."""
    if is_internal_namespace(labels.get("namespace")):
        return False

    service = resolved_service or extract_service_from_labels(labels)
    return is_monitored_service(service)
