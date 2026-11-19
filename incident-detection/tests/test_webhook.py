"""
Alertmanager webhook parsing (ingest.trigger.webhook) — pure, no server/DB.
"""

from __future__ import annotations

from ingest.trigger.webhook import monitored_alerts, parse_alerts


def _payload():
    return {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "ServiceHighErrorRate",
                    "service": "productcatalogservice",
                    "namespace": "otel-demo",
                    "severity": "critical",
                },
                "startsAt": "2026-07-16T12:04:00.000Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "fingerprint": "abc123",
            },
            {
                "status": "firing",
                "labels": {"alertname": "PrometheusDown", "namespace": "prometheus"},
                "startsAt": "2026-07-16T12:04:00.000Z",
                "fingerprint": "def456",
            },
        ],
    }


def test_parse_extracts_fields():
    alerts = parse_alerts(_payload())
    assert len(alerts) == 2
    a = alerts[0]
    assert a.alert_name == "ServiceHighErrorRate"
    assert a.service == "productcatalogservice"
    assert a.namespace == "otel-demo"
    assert a.severity == "critical"
    assert a.starts_at is not None and a.starts_at.tzinfo is not None
    # zero-time endsAt is normalized to None
    assert a.ends_at is None


def test_incident_id_stable_from_fingerprint():
    alerts = parse_alerts(_payload())
    assert alerts[0].incident_id == "inc-abc123-2026-07-16T12:04:00+00:00"
    payload = _payload()
    payload["alerts"][0]["status"] = "resolved"
    assert parse_alerts(payload)[0].incident_id == alerts[0].incident_id
    payload["alerts"][0]["startsAt"] = "2026-07-16T12:14:00Z"
    assert parse_alerts(payload)[0].incident_id != alerts[0].incident_id


def test_monitored_filters_internal_namespace():
    # the prometheus-namespace alert is internal-stack noise → dropped
    alerts = monitored_alerts(_payload())
    names = {a.alert_name for a in alerts}
    assert "ServiceHighErrorRate" in names
    assert "PrometheusDown" not in names


def test_resolved_status():
    payload = _payload()
    payload["alerts"][0]["status"] = "resolved"
    payload["alerts"][0]["endsAt"] = "2026-07-16T12:10:00.000Z"
    a = parse_alerts(payload)[0]
    assert a.is_resolved
    assert a.ends_at is not None
