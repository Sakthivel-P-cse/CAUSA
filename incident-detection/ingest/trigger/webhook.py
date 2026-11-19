"""
webhook.py — parse an Alertmanager webhook payload into typed alerts.

Pure and dependency-free (stdlib + service_scope) so it is unit-testable without
a server or a cluster. The Alertmanager v4 webhook shape:

    { "status": "firing"|"resolved",
      "alerts": [ { "status", "labels": {alertname, service, namespace, node,
                    severity, ...}, "startsAt", "endsAt", "fingerprint" } ] }
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..service_scope import extract_service_from_labels, is_monitored_alert
from runtime import profile as runtime_profile


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    # Alertmanager sends a zero time ("0001-01-01T00:00:00Z") for an unset endsAt.
    if dt.year <= 1:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Alert:
    """One normalized alert from an Alertmanager webhook."""

    alert_name: str
    service: str | None
    namespace: str | None
    node: str | None
    severity: str | None
    status: str                    # "firing" | "resolved"
    starts_at: datetime | None
    ends_at: datetime | None
    fingerprint: str
    labels: dict[str, str]
    annotations: dict[str, str]

    @property
    def is_resolved(self) -> bool:
        return self.status == "resolved"

    @property
    def route(self) -> str | None:
        return self.labels.get("route")

    @property
    def runbook_url(self) -> str | None:
        return self.annotations.get("runbook_url") or self.annotations.get("runbook")

    @property
    def incident_id(self) -> str:
        """
        Stable across notifications of one firing episode, including resolve.
        """
        if self.fingerprint:
            if self.starts_at:
                started = self.starts_at.astimezone(timezone.utc).isoformat()
                return f"inc-{self.fingerprint}-{started}"
            # shortcut: alerts without startsAt cannot distinguish episodes, fix the sender to include startsAt
            return f"inc-{self.fingerprint}"
        svc = self.service or "unknown"
        ts = self.starts_at.isoformat() if self.starts_at else "?"
        return f"inc-{self.alert_name}-{svc}-{ts}"


def _node_from_labels(labels: dict[str, str]) -> str | None:
    if labels.get("node"):
        return labels["node"]
    instance = labels.get("instance")
    if not instance:
        return None
    return instance.rsplit(":", 1)[0] if ":" in instance else instance


def _label_pick(labels: dict[str, str], keys: list[str]) -> str | None:
    for key in keys:
        value = labels.get(key)
        if value:
            return str(value)
    return None


def parse_alerts(payload: dict[str, Any]) -> list[Alert]:
    """Extract every alert from an Alertmanager webhook body."""
    out: list[Alert] = []
    alerts = payload.get("alerts")
    if not isinstance(alerts, list):
        return out
    for raw in alerts:
        if not isinstance(raw, dict):
            continue
        labels = {str(k): str(v) for k, v in (raw.get("labels") or {}).items()}
        annotations = {
            str(k): str(v) for k, v in (raw.get("annotations") or {}).items()
        }
        mapping = runtime_profile.load_profile().alert_mapping
        out.append(
            Alert(
                alert_name=labels.get("alertname", "unknown"),
                service=extract_service_from_labels(labels),
                namespace=_label_pick(labels, mapping.namespace_label_keys) or labels.get("namespace"),
                node=_node_from_labels(labels),
                severity=_label_pick(labels, mapping.severity_label_keys) or labels.get("severity"),
                status=str(raw.get("status") or payload.get("status") or "firing"),
                starts_at=_parse_iso(raw.get("startsAt")),
                ends_at=_parse_iso(raw.get("endsAt")),
                fingerprint=str(raw.get("fingerprint") or ""),
                labels=labels,
                annotations=annotations,
            )
        )
    return out


def monitored_alerts(payload: dict[str, Any]) -> list[Alert]:
    """parse_alerts, filtered to app/service scope (drops internal-stack noise)."""
    return [a for a in parse_alerts(payload) if is_monitored_alert(a.labels, a.service)]


def scoped_forward_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    A shallow copy of the webhook with `alerts` narrowed to monitored ones, so
    Stage-2 never scores internal-stack noise we filtered out on our side.
    """
    alerts_raw = payload.get("alerts")
    if not isinstance(alerts_raw, list):
        return payload
    kept = []
    for raw in alerts_raw:
        if not isinstance(raw, dict):
            continue
        labels = {str(k): str(v) for k, v in (raw.get("labels") or {}).items()}
        if is_monitored_alert(labels, extract_service_from_labels(labels)):
            kept.append(raw)
    scoped = dict(payload)
    scoped["alerts"] = kept
    return scoped
