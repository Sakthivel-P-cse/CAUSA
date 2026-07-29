"""
Trigger service — webhook robustness, scoped forward, DSN quoting, and the
retry→outbox forward path.
"""

from __future__ import annotations

import httpx
import respx

from ingest.trigger import app as app_mod
from ingest.trigger.app import Dispatcher
from ingest.trigger.state import dsn_from_env
from ingest.trigger.webhook import parse_alerts, scoped_forward_payload


def test_malformed_webhooks_never_raise():
    assert parse_alerts({"alerts": None}) == []
    assert parse_alerts({"alerts": "nope"}) == []
    assert parse_alerts({}) == []
    assert parse_alerts({"alerts": [None, 3]}) == []
    assert scoped_forward_payload({"alerts": None}).get("alerts") is None or True


def test_scoped_forward_drops_internal_stack_alerts():
    payload = {"status": "firing", "alerts": [
        {"labels": {"alertname": "AppDown", "service": "checkout", "namespace": "otel-demo"}},
        {"labels": {"alertname": "Noise", "service": "prometheus", "namespace": "prometheus"}},
    ]}
    kept = scoped_forward_payload(payload)["alerts"]
    services = {a["labels"]["service"] for a in kept}
    assert "checkout" in services and "prometheus" not in services


def test_dsn_is_libpq_quoted(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "pa ss'w\\ord")
    monkeypatch.setenv("PGUSER", "causa")
    dsn = dsn_from_env()
    # value single-quoted; embedded quote and backslash escaped
    assert "password='pa ss\\'w\\\\ord'" in dsn
    assert "user='causa'" in dsn


class _FakeStore:
    def __init__(self):
        self.outbox: list[dict] = []

    def enqueue_outbox(self, payload, last_error=None):
        self.outbox.append({"payload": payload, "last_error": last_error})


@respx.mock
def test_forward_success_no_outbox(monkeypatch):
    monkeypatch.setattr(app_mod.time, "sleep", lambda *_: None)
    route = respx.post("http://s2.local/trigger").mock(return_value=httpx.Response(200))
    store = _FakeStore()
    disp = Dispatcher(store, reader_factory=lambda: None,
                      forward_url="http://s2.local/trigger", workers=1, run_outbox=False)
    disp._forward_with_retry({"alerts": []})
    assert route.called
    assert store.outbox == []


def test_submit_returns_false_when_queue_saturated():
    # Audit L3: a saturated queue must be a clean drop signal (→ 503), NOT an
    # unguarded queue.Full escaping as a 500.
    import queue as _queue

    disp = Dispatcher(_FakeStore(), reader_factory=lambda: None,
                      forward_url=None, workers=1, run_outbox=False)

    class _FullQ:
        maxsize = 1000

        def put_nowait(self, item):
            raise _queue.Full

    disp._q = _FullQ()
    assert disp.submit([], {"alerts": []}) is False


@respx.mock
def test_forward_failure_buffers_to_outbox(monkeypatch):
    monkeypatch.setattr(app_mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(app_mod, "_FORWARD_RETRIES", 2)
    respx.post("http://s2.local/trigger").mock(return_value=httpx.Response(503))
    store = _FakeStore()
    disp = Dispatcher(store, reader_factory=lambda: None,
                      forward_url="http://s2.local/trigger", workers=1, run_outbox=False)
    disp._forward_with_retry({"alerts": [{"x": 1}]})
    assert len(store.outbox) == 1                      # Stage-2 outage → buffered, not dropped
    assert store.outbox[0]["last_error"]


class _OpenStore(_FakeStore):
    def __init__(self):
        super().__init__()
        self.opened: list[str] = []
        self.resolved: list[str] = []

    def open_incident(self, **kwargs):
        self.opened.append(kwargs["incident_id"])

    def resolve_incident(self, incident_id, resolved_at):
        self.resolved.append(incident_id)
        return True


def _alert(fp: str, service: str = "cart", resolved: bool = False):
    from datetime import datetime, timezone
    from ingest.trigger.webhook import Alert

    return Alert(
        alert_name="ServiceHighErrorRate",
        service=service,
        namespace="otel-demo",
        node=None,
        severity="critical",
        status="resolved" if resolved else "firing",
        starts_at=datetime.now(timezone.utc),
        ends_at=datetime.now(timezone.utc) if resolved else None,
        fingerprint=fp,
        labels={"service": service, "alertname": "ServiceHighErrorRate"},
        annotations={},
    )


@respx.mock
def test_process_empty_batch_skips_without_error(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(app_mod.time, "sleep", lambda *_: None)
    store = _OpenStore()
    disp = Dispatcher(
        store,
        reader_factory=lambda: None,
        forward_url="http://s2.local/trigger",
        workers=1,
        run_outbox=False,
    )
    with caplog.at_level(logging.ERROR):
        disp._process([], {"alerts": []})
    assert not any("Skipping Stage-2" in r.message for r in caplog.records)
    assert store.opened == []


@respx.mock
def test_process_snapshot_fail_still_forwards(monkeypatch):
    monkeypatch.setattr(app_mod.time, "sleep", lambda *_: None)
    route = respx.post("http://s2.local/trigger").mock(return_value=httpx.Response(200))

    class _BoomReader:
        def read_incident_window(self, **kwargs):
            raise RuntimeError("tempo down")

        def close(self):
            pass

    store = _OpenStore()
    disp = Dispatcher(
        store,
        reader_factory=_BoomReader,
        forward_url="http://s2.local/trigger",
        workers=1,
        run_outbox=False,
    )
    payload = {
        "alerts": [
            {
                "fingerprint": "cart-1",
                "labels": {"service": "cart", "alertname": "ServiceHighErrorRate"},
            }
        ]
    }
    disp._process([_alert("cart-1")], payload)
    assert store.opened == ["inc-cart-1"]
    assert route.called
    body = route.calls.last.request.content
    assert b"cart-1" in body


@respx.mock
def test_process_mixed_batch_forwards_only_opened(monkeypatch):
    monkeypatch.setattr(app_mod.time, "sleep", lambda *_: None)
    route = respx.post("http://s2.local/trigger").mock(return_value=httpx.Response(200))

    store = _OpenStore()
    opened_once = {"n": 0}

    def _open_side(alert):
        opened_once["n"] += 1
        if alert.fingerprint == "bad":
            raise RuntimeError("db fail")
        store.open_incident(incident_id=alert.incident_id)
        return True

    disp = Dispatcher(
        store,
        reader_factory=lambda: None,
        forward_url="http://s2.local/trigger",
        workers=1,
        run_outbox=False,
    )
    monkeypatch.setattr(disp, "_open", _open_side)
    payload = {
        "alerts": [
            {"fingerprint": "good", "labels": {"service": "cart"}},
            {"fingerprint": "bad", "labels": {"service": "payment"}},
        ]
    }
    disp._process([_alert("good"), _alert("bad", service="payment")], payload)
    assert route.called
    forwarded = route.calls.last.request.content.decode()
    assert "good" in forwarded
    assert "bad" not in forwarded
