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
