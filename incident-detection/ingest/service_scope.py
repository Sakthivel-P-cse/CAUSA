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

from runtime import profile as runtime_profile

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
    r"envoy(?:$|-.*)|"  # bare OTel graph peer; use frontend-proxy / app names instead
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
    r"minio(?:$|-.*)|"
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
_UNKNOWN_SERVICE_PREFIX_RE = re.compile(r"^unknown_service:(?P<rest>.+)$", re.IGNORECASE)
_SYSTEM_INFRA_HOST_RE = re.compile(
    r"(?:^|-)(?:control-plane|master)$",
    re.IGNORECASE,
)
_SYSTEM_INFRA_HOST_NAMES = frozenset({
    "kind-control-plane",
    "minikube",
    "docker-desktop",
})


@lru_cache(maxsize=1)
def _monitored_services_re() -> re.Pattern[str] | None:
    raw = (runtime_profile.load_profile().monitored_services_regex or "").strip()
    if not raw:
        return None
    try:
        return re.compile(raw, re.IGNORECASE)
    except re.error:
        return None


@lru_cache(maxsize=1)
def _excluded_services_re() -> re.Pattern[str] | None:
    prof = runtime_profile.load_profile()
    extra = (prof.excluded_services_regex or "").strip()
    pattern = (
        _DEFAULT_EXCLUDED_SERVICES_RE
        if not extra
        else f"(?:{_DEFAULT_EXCLUDED_SERVICES_RE})|(?:{extra})"
    )
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(_DEFAULT_EXCLUDED_SERVICES_RE, re.IGNORECASE)


def normalize_service_name(service: str | None) -> str | None:
    """
    Clean Alertmanager / Prom service-graph labels into a usable service id.

    - `unknown_service:checkout` → `checkout`
    - path-like junk (`/nodejs/bin/node`) → `unknown`
    - bare `unknown_service` → `unknown`
    """
    if not service:
        return None
    value = service.strip()
    if not value:
        return None
    match = _UNKNOWN_SERVICE_PREFIX_RE.match(value)
    if match:
        value = match.group("rest").strip()
    if not value:
        return None
    if value.lower() in {"unknown_service", "unknown", "none", "n/a"}:
        return "unknown"
    if value.startswith("/") or "/" in value:
        return "unknown"
    return value


def is_system_infra_host(name: str | None) -> bool:
    """True for Kind/k8s control-plane style hosts that must not win app RCA."""
    if not name:
        return False
    value = name.strip()
    if not value:
        return False
    if value.lower() in _SYSTEM_INFRA_HOST_NAMES:
        return True
    return bool(_SYSTEM_INFRA_HOST_RE.search(value))


def _service_name_label_keys() -> list[str]:
    keys = runtime_profile.load_profile().alert_mapping.service_label_keys
    return [k.strip() for k in keys if k.strip()]


def monitored_services_pattern() -> str | None:
    raw = (runtime_profile.load_profile().monitored_services_regex or "").strip()
    return raw or None


def excluded_services_pattern() -> str:
    extra = (runtime_profile.load_profile().excluded_services_regex or "").strip()
    return (
        _DEFAULT_EXCLUDED_SERVICES_RE
        if not extra
        else f"(?:{_DEFAULT_EXCLUDED_SERVICES_RE})|(?:{extra})"
    )


def extract_service_from_labels(labels: dict[str, str]) -> str | None:
    """Best-effort service extraction from Alertmanager / stream labels."""
    raw: str | None = None
    for key in _service_name_label_keys():
        if val := labels.get(key):
            raw = val
            break

    if raw is None:
        if server := labels.get("server"):
            raw = server
        elif pod := labels.get("pod"):
            parts = pod.rsplit("-", 2)
            raw = parts[0] if len(parts) >= 3 else pod
        elif inst := labels.get("instance"):
            raw = inst
        else:
            raw = labels.get("namespace")

    return normalize_service_name(raw)

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
    names = set(_INTERNAL_NAMESPACES) | {x.lower() for x in runtime_profile.load_profile().internal_namespaces}
    return namespace.strip().lower() in names


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


_PLATFORM_LAYERS = frozenset({"platform", "slo"})
_PLATFORM_ALERT_RE = re.compile(
    r"^(?:"
    r"Minio|"
    r"Loki|"
    r"Tempo|"
    r"Mimir|"
    r"Otel|"
    r"Prometheus|"
    r"Alertmanager|"
    r"PersistentVolume|"
    r"HPA|"
    r"SLO"
    r")",
    re.IGNORECASE,
)


def is_monitored_alert(labels: dict[str, str], resolved_service: str | None = None) -> bool:
    """True if an alert belongs to external/app service scope."""
    if is_internal_namespace(labels.get("namespace")):
        return False

    layer = str(labels.get("layer") or "").strip().lower()
    if layer in _PLATFORM_LAYERS:
        return False

    alert_name = str(labels.get("alertname") or "").strip()
    if alert_name and _PLATFORM_ALERT_RE.match(alert_name):
        return False

    service = resolved_service or extract_service_from_labels(labels)
    return is_monitored_service(service)
