"""Stage-2 HTTP service exposing POST /trigger and GET /health."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ingest.trigger.webhook import monitored_alerts
from rca.orchestrator import RCAOrchestrator
from rca.store import RCAStore

logger = logging.getLogger(__name__)

_MAX_BODY = int(os.getenv("TRIGGER_MAX_BODY_BYTES", str(1024 * 1024)))
incident_not_opened_via_ingest_total = 0
rca_process_failures_total = 0


class Dispatcher:
    """Persist accepted incidents and process them in background workers."""

    def __init__(self, orchestrator: RCAOrchestrator, *, workers: int = 2):
        self.orchestrator = orchestrator
        for index in range(max(1, workers)):
            threading.Thread(target=self._run, name=f"rca-worker-{index}", daemon=True).start()

    def submit(self, incident_ids: list[str]) -> bool:
        try:
            self.orchestrator.store.enqueue_trigger(list(dict.fromkeys(incident_ids)))
            return True
        except Exception:
            logger.exception("Failed to persist RCA trigger")
            return False

    def _run(self) -> None:
        global incident_not_opened_via_ingest_total, rca_process_failures_total
        while True:
            try:
                if not self.orchestrator.store.process_trigger(self.orchestrator.process):
                    time.sleep(1)
            except LookupError:
                incident_not_opened_via_ingest_total += 1
                logger.exception("RCA incident not opened via ingest; retrying")
                time.sleep(5)
            except Exception:
                rca_process_failures_total += 1
                logger.exception("RCA trigger processing failed; retrying")
                time.sleep(5)


class TriggerHandler(BaseHTTPRequestHandler):
    dispatcher: Dispatcher

    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        logger.debug("%s - " + fmt, self.address_string(), *args)

    def do_GET(self) -> None:
        path = self.path.rstrip("/")
        if path == "/ready":
            try:
                self.dispatcher.orchestrator.store.check_available()
                self._send(200, {"status": "ready"})
            except Exception:
                logger.exception("readiness check failed")
                self._send(503, {"status": "not_ready", "postgres": False})
        elif path in ("", "/health", "/healthz"):
            self._send(
                200,
                {
                    "status": "ok",
                    "incident_not_opened_via_ingest_total": incident_not_opened_via_ingest_total,
                    "rca_process_failures_total": rca_process_failures_total,
                },
            )
        elif path == "/metrics":
            body = (
                f"# TYPE incident_not_opened_via_ingest_total counter\n"
                f"incident_not_opened_via_ingest_total {incident_not_opened_via_ingest_total}\n"
                f"# TYPE rca_process_failures_total counter\n"
                f"rca_process_failures_total {rca_process_failures_total}\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/trigger":
            self._send(404, {"error": "not found"})
            return
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._send(411, {"error": "length required"})
            return
        try:
            length = int(raw_length)
        except ValueError:
            self._send(400, {"error": "invalid content-length"})
            return
        if length < 0:
            self._send(400, {"error": "invalid content-length"})
            return
        if length > _MAX_BODY:
            self._send(413, {"error": "payload too large"})
            return

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._send(400, {"error": "expected a JSON object"})
            return

        try:
            alerts = monitored_alerts(payload)
        except Exception as exc:
            logger.warning("Malformed webhook payload: %s", exc)
            self._send(400, {"error": "malformed payload"})
            return
        incident_ids = list(
            dict.fromkeys(alert.incident_id for alert in alerts if not alert.is_resolved)
        )
        if self.dispatcher.submit(incident_ids):
            self._send(202, {"accepted": incident_ids})
        else:
            self._send(503, {"error": "trigger busy, retry later"})


def make_server(
    port: int = 8080,
    *,
    orchestrator: RCAOrchestrator | None = None,
    workers: int | None = None,
) -> ThreadingHTTPServer:
    TriggerHandler.dispatcher = Dispatcher(
        orchestrator or RCAOrchestrator(RCAStore()),
        workers=workers
        if workers is not None
        else int(os.getenv("TRIGGER_WORKERS", "2")),
    )
    return ThreadingHTTPServer(("0.0.0.0", port), TriggerHandler)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    port = int(os.getenv("TRIGGER_PORT", "8080"))
    logger.info("Causa Stage-2 RCA trigger listening on :%d", port)
    make_server(port).serve_forever()


if __name__ == "__main__":
    main()
