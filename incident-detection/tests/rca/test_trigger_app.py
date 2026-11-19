import http.client
import json
import threading
import queue

from rca.trigger.app import make_server


class _QueueStore:
    def __init__(self):
        self.queue = queue.Queue()

    def enqueue_trigger(self, ids):
        for item in ids:
            self.queue.put(item)

    def process_trigger(self, process):
        try:
            item = self.queue.get_nowait()
        except queue.Empty:
            return False
        try:
            process(item)
        finally:
            self.queue.task_done()
        return True

    def wait(self):
        self.queue.join()


class _Orchestrator:
    def __init__(self):
        self.store = _QueueStore()
        self.processed = []

    def process(self, incident_id):
        self.processed.append(incident_id)


def _request(server, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    encoded = json.dumps(body).encode() if body is not None else None
    headers = {}
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
        orchestrator.store.wait()
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
