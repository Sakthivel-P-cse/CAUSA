"""
app.py — the RCA-trigger HTTP service (/trigger + /health).

Request path (fast): parse + scope-filter the Alertmanager webhook, then ACK
immediately (202). The heavy work — window snapshot, incident persistence, and
the Stage-2 forward — runs on a background worker so the HTTP response never
blocks on a slow backend. Alertmanager's webhook has a short timeout; a slow
synchronous handler would time out and re-deliver, double-scoring the incident.

Worker path (durable): per firing alert, snapshot the C1 window + persist the
open incident; on a resolved alert, mark it resolved. Then forward the original
webhook to Stage-2 with bounded retry; if Stage-2 is still unreachable the
webhook is buffered to a Postgres outbox and drained later, so a Stage-2 outage
cannot silently drop an incident.

Stdlib HTTP server (no framework) — runtime deps stay httpx + psycopg.

Env config:
  PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD   Postgres (see state.dsn_from_env)
  STAGE2_WEBHOOK_URL         forward target (default in-cluster stage2-rca)
  STAGE2_FORWARD_RETRIES     inline forward attempts before outbox (default 3)
  STAGE2_FORWARD_TIMEOUT     per-attempt forward timeout seconds (default 5)
  STAGE2_OUTBOX_INTERVAL     outbox drain period seconds (default 30)
  PROMETHEUS_URL / TEMPO_URL / LOKI_URL        backends for the reader
  CAUSA_APP_NAMESPACE        namespace for container CPU/mem metrics
  CAUSA_LOOKBACK_MIN         window lookback minutes (default 10)
  TRIGGER_MAX_BODY_BYTES     max accepted request body (default 1 MiB)
  TRIGGER_WORKERS            background worker threads (default 2)
  TRIGGER_PORT               listen port (default 8080)
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..reader import TelemetryReader
from .state import IncidentStore
from .webhook import Alert, monitored_alerts, scoped_forward_payload

logger = logging.getLogger(__name__)

_STAGE2_URL = os.getenv(
    "STAGE2_WEBHOOK_URL", "http://stage2-rca.stage2-rca.svc.cluster.local:8080/trigger"
)
_LOOKBACK_MIN = int(os.getenv("CAUSA_LOOKBACK_MIN", "10"))
_NAMESPACE = os.getenv("CAUSA_APP_NAMESPACE") or None
_MAX_BODY = int(os.getenv("TRIGGER_MAX_BODY_BYTES", str(1024 * 1024)))
_FORWARD_RETRIES = int(os.getenv("STAGE2_FORWARD_RETRIES", "3"))
_FORWARD_TIMEOUT = float(os.getenv("STAGE2_FORWARD_TIMEOUT", "5.0"))
_OUTBOX_INTERVAL = float(os.getenv("STAGE2_OUTBOX_INTERVAL", "30"))

ingest_snapshot_failed_total = 0
ingest_queue_full_total = 0
ingest_outbox_enqueued_total = 0
ingest_outbox_sent_total = 0
ingest_outbox_lost_total = 0


def _default_reader_factory() -> TelemetryReader:
    return TelemetryReader.from_defaults(
        prometheus_url=os.getenv(
            "PROMETHEUS_URL", "http://prometheus.prometheus.svc.cluster.local:9090"
        ),
        tempo_url=os.getenv("TEMPO_URL", "http://tempo.tempo.svc.cluster.local:3200"),
        loki_url=os.getenv("LOKI_URL", "http://loki.loki.svc.cluster.local:3100"),
    )


class Dispatcher:
    """
    Background processing for accepted webhooks. The request thread only enqueues
    (alerts, payload); worker threads do the slow snapshot/persist/forward, and a
    drain thread retries the durable Stage-2 outbox.
    """

    def __init__(
        self,
        store: IncidentStore,
        reader_factory,
        forward_url: str | None,
        *,
        workers: int = 2,
        run_outbox: bool = True,
    ):
        self.store = store
        self.reader_factory = reader_factory
        self.forward_url = forward_url
        self._q: queue.Queue = queue.Queue(maxsize=1000)
        for i in range(max(1, workers)):
            threading.Thread(
                target=self._run, name=f"trigger-worker-{i}", daemon=True
            ).start()
        if run_outbox and forward_url:
            threading.Thread(
                target=self._drain_outbox_loop, name="trigger-outbox", daemon=True
            ).start()

    def submit(self, alerts: list[Alert], payload: dict) -> bool:
        global ingest_queue_full_total
        try:
            self._q.put_nowait((alerts, payload))
            return True
        except queue.Full:
            ingest_queue_full_total += 1
            logger.warning(
                "Trigger queue saturated (maxsize=%d) — 503 for Alertmanager retry.",
                self._q.maxsize,
            )
            return False

    def _run(self) -> None:
        while True:
            alerts, payload = self._q.get()
            try:
                self._process(alerts, payload)
            except Exception:
                logger.exception("Worker failed processing webhook")
            finally:
                self._q.task_done()

    def _process(self, alerts: list[Alert], payload: dict) -> None:
        if not alerts:
            logger.info("No monitored alerts — skip open/forward")
            return

        opened_ids: set[str] = set()
        firing = 0
        for alert in alerts:
            try:
                if alert.is_resolved:
                    self.store.resolve_incident(
                        alert.incident_id, alert.ends_at or datetime.now(timezone.utc)
                    )
                else:
                    firing += 1
                    if self._open(alert):
                        opened_ids.add(alert.incident_id)
            except Exception:
                logger.exception("Failed handling alert %s", alert.alert_name)

        if not opened_ids:
            if firing:
                logger.error(
                    "Skipping Stage-2 forward — %d firing alert(s) failed to open",
                    firing,
                )
            return

        scoped = dict(payload)
        raw_alerts = payload.get("alerts") or []
        scoped["alerts"] = [
            a
            for a in raw_alerts
            if isinstance(a, dict)
            and f"inc-{a.get('fingerprint') or ''}" in opened_ids
        ]
        self._forward_with_retry(scoped)

    def _open(self, alert: Alert) -> bool:
        global ingest_snapshot_failed_total
        around = alert.starts_at or datetime.now(timezone.utc)
        window_start = around - timedelta(minutes=_LOOKBACK_MIN)
        window_end = around + timedelta(minutes=1)
        trace_id = None
        refs: dict = {}
        snapshot_status = "ok"
        alert_meta = {
            "route": alert.route,
            "runbook_url": alert.runbook_url,
            "layer": alert.labels.get("layer"),
            "slo_target": alert.labels.get("slo_target"),
        }
        alert_meta = {k: v for k, v in alert_meta.items() if v}
        try:
            reader = self.reader_factory()
            telem = reader.read_incident_window(
                incident_id=alert.incident_id,
                alerted_service=alert.service or "unknown",
                around=around,
                lookback_minutes=_LOOKBACK_MIN,
                namespace=alert.namespace or _NAMESPACE,
            )
            trace_id = telem.trace_id
            refs = telem.to_summary()
            reader.close()
        except Exception as exc:
            ingest_snapshot_failed_total += 1
            snapshot_status = "failed"
            logger.error(
                "Window snapshot failed for %s: %s (snapshot_failed_total=%d)",
                alert.incident_id,
                exc,
                ingest_snapshot_failed_total,
            )
            self.store.open_incident(
                incident_id=alert.incident_id,
                alert_name=alert.alert_name,
                alerted_service=alert.service,
                namespace=alert.namespace or _NAMESPACE,
                node=alert.node,
                severity=alert.severity,
                window_start=window_start,
                window_end=window_end,
                trace_id=None,
                telemetry_refs=alert_meta,
                snapshot_status=snapshot_status,
            )
            # Still open + forward Stage-2 even when the telemetry snapshot fails;
            # otherwise AppLogic/error-rate alerts never become RCA incidents.
            return True

        refs = {**refs, **alert_meta}
        self.store.open_incident(
            incident_id=alert.incident_id,
            alert_name=alert.alert_name,
            alerted_service=alert.service,
            namespace=alert.namespace or _NAMESPACE,
            node=alert.node,
            severity=alert.severity,
            window_start=window_start,
            window_end=window_end,
            trace_id=trace_id,
            telemetry_refs=refs,
            snapshot_status=snapshot_status,
        )
        return True

    def _forward_with_retry(self, payload: dict) -> None:
        global ingest_outbox_enqueued_total, ingest_outbox_lost_total
        if not self.forward_url:
            return
        import httpx

        last_error = ""
        for attempt in range(1, _FORWARD_RETRIES + 1):
            try:
                resp = httpx.post(
                    self.forward_url,
                    json=payload,
                    timeout=_FORWARD_TIMEOUT,
                )
                resp.raise_for_status()
                return
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Stage-2 forward attempt %d/%d failed: %s",
                    attempt, _FORWARD_RETRIES, exc,
                )
                if attempt < _FORWARD_RETRIES:
                    time.sleep(min(2 ** attempt, 8))
        try:
            self.store.enqueue_outbox(payload, last_error=last_error)
            ingest_outbox_enqueued_total += 1
        except Exception:
            ingest_outbox_lost_total += 1
            logger.exception("Failed to buffer Stage-2 forward to outbox — LOST")

    def _drain_outbox_loop(self) -> None:
        global ingest_outbox_sent_total
        import httpx

        while True:
            time.sleep(_OUTBOX_INTERVAL)
            try:
                rows = self.store.pending_outbox(limit=50)
            except Exception:
                logger.exception("Outbox read failed")
                continue
            for row in rows:
                try:
                    resp = httpx.post(
                        self.forward_url,
                        json=row["payload"],
                        timeout=_FORWARD_TIMEOUT,
                    )
                    resp.raise_for_status()
                    self.store.mark_outbox_sent(row["id"])
                    ingest_outbox_sent_total += 1
                    logger.info("Drained outbox row %s to Stage-2", row["id"])
                except Exception as exc:
                    self.store.bump_outbox_attempt(row["id"], str(exc))


class TriggerHandler(BaseHTTPRequestHandler):
    # Injected by make_server():
    dispatcher: Dispatcher

    def _send(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:  # quiet the default stderr spam
        logger.debug("%s - " + fmt, self.address_string(), *args)

    def do_GET(self) -> None:
        path = self.path.rstrip("/")
        if path == "/ready":
            try:
                self.dispatcher.store.check_available()
                self._send(200, {"status": "ready"})
            except Exception:
                logger.exception("readiness check failed")
                self._send(503, {"status": "not_ready", "postgres": False})
        elif path in ("/health", "/healthz", ""):
            self._send(
                200,
                {
                    "status": "ok",
                    "ingest_snapshot_failed_total": ingest_snapshot_failed_total,
                    "ingest_queue_full_total": ingest_queue_full_total,
                    "ingest_outbox_enqueued_total": ingest_outbox_enqueued_total,
                    "ingest_outbox_sent_total": ingest_outbox_sent_total,
                    "ingest_outbox_lost_total": ingest_outbox_lost_total,
                },
            )
        elif path == "/metrics":
            body = (
                f"# TYPE ingest_snapshot_failed_total counter\n"
                f"ingest_snapshot_failed_total {ingest_snapshot_failed_total}\n"
                f"# TYPE ingest_queue_full_total counter\n"
                f"ingest_queue_full_total {ingest_queue_full_total}\n"
                f"# TYPE ingest_outbox_enqueued_total counter\n"
                f"ingest_outbox_enqueued_total {ingest_outbox_enqueued_total}\n"
                f"# TYPE ingest_outbox_sent_total counter\n"
                f"ingest_outbox_sent_total {ingest_outbox_sent_total}\n"
                f"# TYPE ingest_outbox_lost_total counter\n"
                f"ingest_outbox_lost_total {ingest_outbox_lost_total}\n"
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
        # Content-Length: required, numeric, and within the body cap. A bad or
        # oversized length gets a clean 4xx instead of a hung/closed socket.
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            self._send(411, {"error": "length required"})
            return
        try:
            length = int(raw_len)
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
            body = self.rfile.read(length) or b"{}"
        except Exception as exc:
            logger.warning("Failed reading request body: %s", exc)
            self._send(400, {"error": "unreadable body"})
            return
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
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

        # ACK fast; the dispatcher does snapshot/persist/forward off-thread.
        # Forward only the monitored alerts (scope-filtered) to Stage-2. On a
        # saturated queue, answer 503 so Alertmanager re-delivers (not a 500,
        # not a silent drop).
        if self.dispatcher.submit(alerts, scoped_forward_payload(payload)):
            self._send(202, {"accepted": [a.incident_id for a in alerts]})
        else:
            self._send(503, {"error": "trigger busy, retry later"})


def make_server(
    port: int = 8080,
    *,
    store: IncidentStore | None = None,
    reader_factory=_default_reader_factory,
    forward_url: str | None = _STAGE2_URL,
    workers: int | None = None,
) -> ThreadingHTTPServer:
    handler = TriggerHandler
    handler.dispatcher = Dispatcher(
        store=store or IncidentStore(),
        reader_factory=reader_factory,
        forward_url=forward_url,
        workers=workers if workers is not None else int(os.getenv("TRIGGER_WORKERS", "2")),
    )
    return ThreadingHTTPServer(("0.0.0.0", port), handler)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    port = int(os.getenv("TRIGGER_PORT", "8080"))
    server = make_server(port)
    logger.info("Causa ingest trigger listening on :%d → forwarding to %s", port, _STAGE2_URL)
    server.serve_forever()


if __name__ == "__main__":
    main()
