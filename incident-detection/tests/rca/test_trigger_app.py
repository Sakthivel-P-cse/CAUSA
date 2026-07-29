import http.client
import json
import threading

import pytest

from rca.trigger import app as app_mod
from rca.trigger.app import make_server


class _Orchestrator:
    def __init__(self):
        self.processed = []

    def process(self, incident_id):
        self.processed.append(incident_id)


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    monkeypatch.setattr(app_mod, "_AUTH_TOKEN", "test-trigger-token")


def _request(server, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    encoded = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": "Bearer test-trigger-token"}
    if encoded is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=encoded, headers=headers)
    response = conn.getresponse()
    payload = json.loads(response.read())
    conn.close()
    return response.status, payload


def test_health_and_trigger_acceptance():
    orchestrator = _Orchestrator()
    server = make_server(0, orchestrator=orchestrator, workers=1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _request(server, "GET", "/health")
        assert status == 200
        assert body["status"] == "ok"
        assert "incident_not_opened_via_ingest_total" in body

        payload = {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "fingerprint": "abc",
                    "labels": {
                        "alertname": "ServiceHighErrorRate",
                        "service": "checkout",
                        "namespace": "otel-demo",
                    },
                }
            ],
        }
        status, body = _request(server, "POST", "/trigger", payload)
        assert status == 202
        assert body == {"accepted": ["inc-abc"]}
        server.RequestHandlerClass.dispatcher._q.join()
        assert orchestrator.processed == ["inc-abc"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_trigger_rejects_invalid_or_malformed_payload():
    orchestrator = _Orchestrator()
    server = make_server(0, orchestrator=orchestrator, workers=1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        conn.request(
            "POST",
            "/trigger",
            body=b"{",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test-trigger-token",
            },
        )
        response = conn.getresponse()
        assert response.status == 400
        response.read()
        conn.close()

        status, body = _request(
            server,
            "POST",
            "/trigger",
            {"alerts": [{"labels": ["not", "a", "mapping"]}]},
        )
        assert status == 400
        assert body == {"error": "malformed payload"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
