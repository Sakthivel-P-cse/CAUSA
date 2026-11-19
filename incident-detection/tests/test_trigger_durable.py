from ingest.trigger.app import Dispatcher
from ingest.trigger.state import IncidentStore
from contextlib import nullcontext
import pytest


def test_submit_persists_before_accepting():
    class Store:
        def __init__(self):
            self.payloads = []

        def enqueue_webhook(self, payload):
            self.payloads.append(payload)

    dispatcher = Dispatcher.__new__(Dispatcher)
    dispatcher.store = Store()
    payload = {"alerts": [{"fingerprint": "abc"}]}
    assert dispatcher.submit([], payload)
    assert dispatcher.store.payloads == [payload]


def test_submit_rejects_when_persistence_fails():
    class Store:
        def enqueue_webhook(self, payload):
            raise RuntimeError("database unavailable")

    dispatcher = Dispatcher.__new__(Dispatcher)
    dispatcher.store = Store()
    assert not dispatcher.submit([], {"alerts": []})


def test_claim_deletes_only_after_success(monkeypatch):
    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.deleted = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def transaction(self):
            return nullcontext()

        def execute(self, sql, *args):
            if "pg_try_advisory" in sql:
                return Result((True,))
            if "SELECT id" in sql:
                return Result((7, {"alerts": []}))
            if "DELETE" in sql:
                self.deleted = True

    conn = Connection()
    store = IncidentStore("unused")
    monkeypatch.setattr(store, "_connect", lambda: conn)
    with pytest.raises(RuntimeError):
        with store.claim_webhook() as payload:
            assert payload == {"alerts": []}
            raise RuntimeError("processing failed")
    assert not conn.deleted
    with store.claim_webhook() as payload:
        assert payload == {"alerts": []}
    assert conn.deleted


def test_processing_failure_is_retried():
    dispatcher = Dispatcher.__new__(Dispatcher)
    dispatcher.store = object()
    dispatcher._open = lambda alert: (_ for _ in ()).throw(RuntimeError("database unavailable"))
    from ingest.trigger.webhook import parse_alerts
    payload = {"alerts": [{"fingerprint": "abc", "labels": {"alertname": "Down", "service": "cart"}}]}
    with pytest.raises(RuntimeError, match="alerts failed"):
        dispatcher._process(parse_alerts(payload), payload)
