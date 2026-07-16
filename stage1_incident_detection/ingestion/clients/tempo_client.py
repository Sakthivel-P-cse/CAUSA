"""
tempo_client.py — Fetch traces from Tempo.

Talks to: tempo.tempo.svc.cluster.local:3200
Feeds:    SpanEnvelope / trace-execution graph.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..contract import TraceSpan

logger = logging.getLogger(__name__)

# Tempo status codes → human label
_STATUS = {0: "unset", 1: "ok", 2: "error"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TempoClient:
    """Thin wrapper around the Tempo HTTP API."""

    def __init__(self, base_url: str = "http://tempo.tempo.svc.cluster.local:3200"):
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(
            timeout=15.0,
            transport=httpx.HTTPTransport(retries=3),
        )

    def close(self) -> None:
        self._http.close()

    # ── Fetch a single trace by ID ─────────────────────────────────────

    def get_trace(self, trace_id: str) -> list[TraceSpan]:
        """
        Fetch a complete trace and return all spans as TraceSpan objects.
        Raises httpx.HTTPStatusError if the trace does not exist.
        """
        resp = self._http.get(f"{self._base}/api/traces/{trace_id}")
        resp.raise_for_status()
        return self._parse_spans(resp.json())

    # ── Search for error traces for a service ─────────────────────────

    def search_error_traces(
        self,
        service: str | None,
        lookback_minutes: int = 10,
        limit: int = 5,
    ) -> list[str]:
        """
        Search Tempo for recent error traces from a given service.
        Returns a list of trace IDs (most recent first).
        """
        end = _utcnow()
        start = end - timedelta(minutes=lookback_minutes)

        tag_parts = []
        if service:
            tag_parts.append(f"service.name={service}")
        tag_parts.append("status=error")
        params = {
            "tags": " ".join(tag_parts),
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
            "limit": limit,
        }
        try:
            resp = self._http.get(f"{self._base}/api/search", params=params)
            resp.raise_for_status()
            data = resp.json()
            traces = data.get("traces", [])
            return [t["traceID"] for t in traces if t.get("traceID")]
        except Exception as exc:
            logger.warning("Tempo search failed for %s: %s", service, exc)
            return []

    def search_traces(
        self,
        service: str | None,
        lookback_minutes: int = 10,
        limit: int = 5,
    ) -> list[str]:
        """
        Search Tempo for recent traces from a given service (all statuses).
        Returns a list of trace IDs (most recent first).
        """
        end = _utcnow()
        start = end - timedelta(minutes=lookback_minutes)

        params = {
            "tags": f"service.name={service}" if service else "",
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
            "limit": limit,
        }
        try:
            resp = self._http.get(f"{self._base}/api/search", params=params)
            resp.raise_for_status()
            data = resp.json()
            traces = data.get("traces", [])
            return [t["traceID"] for t in traces if t.get("traceID")]
        except Exception as exc:
            logger.warning("Tempo search failed for %s: %s", service, exc)
            return []

    # ── Parse Tempo response into TraceSpan objects ────────────────────

    @staticmethod
    def _parse_spans(raw: dict[str, Any]) -> list[TraceSpan]:
        spans: list[TraceSpan] = []
        for resource_span in raw.get("batches", []):
            # Extract service name from resource attributes
            service = "unknown"
            for attr in resource_span.get("resource", {}).get("attributes", []):
                if attr.get("key") == "service.name":
                    service = attr["value"].get("stringValue", "unknown")
                    break

            for scope_span in resource_span.get("scopeSpans", [{"spans": []}]):
                for span in scope_span.get("spans", []):
                    # Parse status
                    status_raw = span.get("status", {})
                    status_code = status_raw.get("code", 0)

                    # Error message from status or span events
                    error_msg = status_raw.get("message")
                    if not error_msg and status_code == 2:
                        for event in span.get("events", []):
                            for attr in event.get("attributes", []):
                                if attr.get("key") in ("exception.message", "error.message"):
                                    error_msg = attr["value"].get("stringValue")
                                    break

                    start_ns: int = int(span.get("startTimeUnixNano", 0))
                    end_ns: int = int(span.get("endTimeUnixNano", 0))
                    duration_ms = (end_ns - start_ns) / 1_000_000

                    operation = span.get("name", "unknown")

                    spans.append(TraceSpan(
                        span_id=span.get("spanId", ""),
                        parent_span_id=span.get("parentSpanId") or None,
                        service=service,
                        operation=operation,
                        duration_ms=duration_ms,
                        status_code=status_code,
                        error_message=error_msg,
                        start_time=datetime.utcfromtimestamp(start_ns / 1_000_000_000),
                    ))
        logger.debug("Parsed %d spans", len(spans))
        return spans
