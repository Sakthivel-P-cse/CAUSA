"""Minimal Dev C dashboard — live join metrics over HTTP."""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


def _summary() -> dict:
    try:
        from eval.live_store import LiveEvalStore
        from eval.live_runner import evaluate_rows

        rows = LiveEvalStore().join_reports_groundtruth()
        return {
            "status": "ok",
            "joined_incidents": len(rows),
            **evaluate_rows(rows),
        }
    except Exception as exc:
        logger.exception("dashboard summary failed")
        return {"status": "error", "error": str(exc)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        logger.debug(fmt, *args)

    def _send(self, code: int, body: dict | str, ctype: str = "application/json") -> None:
        raw = body if isinstance(body, (bytes, bytearray)) else (
            body.encode() if isinstance(body, str) else json.dumps(body).encode()
        )
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/health", "/healthz"):
            self._send(200, {"status": "ok"})
        elif path in ("/summary", "/api/summary"):
            self._send(200, _summary())
        elif path == "/":
            html = (
                "<!doctype html><html><head><title>Causa Eval</title></head>"
                "<body><h1>Causa Eval Dashboard</h1>"
                "<p><a href='/summary'>/summary</a> JSON</p></body></html>"
            )
            self._send(200, html, "text/html")
        else:
            self._send(404, {"error": "not found"})


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    port = int(os.getenv("DASHBOARD_PORT", "8090"))
    logger.info("causa-dashboard listening on :%d", port)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
