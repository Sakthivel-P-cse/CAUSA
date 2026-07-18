"""Stage-2 HTTP service exposing POST /trigger and GET /health."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ingest.trigger.webhook import monitored_alerts
from rca.orchestrator import RCAOrchestrator
from rca.store import RCAStore

logger = logging.getLogger(__name__)

_MAX_BODY = int(os.getenv("TRIGGER_MAX_BODY_BYTES", str(1024 * 1024)))
_AUTH_TOKEN = os.getenv("TRIGGER_AUTH_TOKEN") or None


class Dispatcher:
    """Bounded background execution; a successful submit means accepted."""

    def __init__(
        self,
        orchestrator: RCAOrchestrator,
        *,
        workers: int = 2,
        queue_size: int = 1000,
    ):
        self.orchestrator = orchestrator
        self._q: queue.Queue[str] = queue.Queue(maxsize=queue_size)
        self._submit_lock = threading.Lock()
        for index in range(max(1, workers)):
            threading.Thread(
                target=self._run,
                name=f"rca-worker-{index}",
                daemon=True,
            ).start()

    def submit(self, incident_ids: list[str]) -> bool:
        with self._submit_lock:
            if (
                self._q.maxsize
                and self._q.qsize() + len(incident_ids) > self._q.maxsize
            ):
                return False
            for incident_id in incident_ids:
                self._q.put_nowait(incident_id)
        return True

    def _run(self) -> None:
        while True:
            incident_id = self._q.get()
            try:
                self.orchestrator.process(incident_id)
            except Exception:
                logger.exception("RCA failed for incident %s", incident_id)
            finally:
                self._q.task_done()


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
        if self.path.rstrip("/") in ("", "/health", "/healthz"):
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/trigger":
            self._send(404, {"error": "not found"})
            return
        if _AUTH_TOKEN and self.headers.get("Authorization") != f"Bearer {_AUTH_TOKEN}":
            self._send(401, {"error": "unauthorized"})
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
