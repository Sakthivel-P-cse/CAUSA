"""
tempo_client.py — Fetch traces from Tempo.

Talks to: tempo.tempo.svc.cluster.local:3200
Feeds:    SpanEnvelope / trace-execution graph.

Captures span `kind` + attributes so ingestion can derive edge `call_type`
(sync|async) for Dev B's Influence Graph (RCA_Engine_Spec §1.2). Dropping these
was the single biggest gap in the previous version.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from contracts import SpanKind, TraceSpan

logger = logging.getLogger(__name__)

# Tempo status codes → human label
_STATUS = {0: "unset", 1: "ok", 2: "error"}

# Span attribute keys we retain (call_type derivation + explainability). We keep
# the whole flattened attribute dict, but these are the load-bearing ones.
_KEPT_ATTR_PREFIXES = (
    "messaging.",   # kafka/rabbitmq → async
    "rpc.",         # grpc → sync
    "db.",          # db call → sync
    "http.",        # http call → sync
    "net.peer.",    # callee resolution
    "server.",      # callee resolution (newer semconv)
    "peer.",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _attr_value(value: Any) -> str:
    """
    Unwrap an OTLP AnyValue to a string. Covers the full AnyValue union
    (string/int/double/bool/bytes/array/kvlist) and real JSON `null` — an
    explicit null becomes "" (empty), NOT the literal string "None".
    """
    if value is None:
        return ""
    if not isinstance(value, dict):
        return str(value)
    for key in ("stringValue", "intValue", "doubleValue", "boolValue", "bytesValue"):
        if key in value:
            inner = value[key]
            return "" if inner is None else str(inner)
    if "arrayValue" in value:
        vals = value["arrayValue"].get("values", [])
        return ",".join(_attr_value(v) for v in vals)
    if "kvlistValue" in value:
        pairs = value["kvlistValue"].get("values", [])
        return ",".join(
            f"{p.get('key', '')}={_attr_value(p.get('value'))}" for p in pairs
        )
    return ""


def _flatten_attributes(span: dict[str, Any]) -> dict[str, str]:
    """OTLP `attributes: [{key, value}]` → flat {key: str}, keeping useful keys."""
    out: dict[str, str] = {}
    for attr in span.get("attributes", []):
        key = attr.get("key")
        if not key:
            continue
        if key.startswith(_KEPT_ATTR_PREFIXES):
            out[key] = _attr_value(attr.get("value", {}))
    return out


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

    # ── Search for traces ──────────────────────────────────────────────

    def search_error_traces(
        self,
        service: str | None,
        lookback_minutes: int = 10,
        limit: int = 5,
    ) -> list[str]:
        """Search Tempo for recent ERROR trace IDs from a service (most recent first)."""
        end = _utcnow()
        start = end - timedelta(minutes=lookback_minutes)
        tags = []
        if service:
            tags.append(f"service.name={service}")
        tags.append("status=error")
        return self._search(" ".join(tags), start, end, limit)

    def search_traces(
        self,
        service: str | None,
        lookback_minutes: int = 10,
        limit: int = 5,
    ) -> list[str]:
        """Search Tempo for recent trace IDs from a service (all statuses)."""
        end = _utcnow()
        start = end - timedelta(minutes=lookback_minutes)
        tags = f"service.name={service}" if service else ""
        return self._search(tags, start, end, limit)

    def search_traces_in_window(
        self,
        service: str | None,
        start: datetime,
        end: datetime,
        limit: int = 20,
    ) -> list[str]:
        """
        Window-parameterized trace search — the query path behind the C1
        `traces(window)` adapter (explicit bounds, not "last N minutes").
        """
        tags = f"service.name={service}" if service else ""
        return self._search(tags, start, end, limit)

    def _search(
        self,
        tags: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[str]:
        params = {
            "tags": tags,
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
            logger.warning("Tempo search failed (tags=%r): %s", tags, exc)
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

                    start_ns: int = int(span.get("startTimeUnixNano", 0) or 0)
                    end_ns: int = int(span.get("endTimeUnixNano", 0) or 0)
                    # A missing/zero start stamps a 1970 start_time that poisons
                    # causal ordering (S3) and windowing — drop the span rather
                    # than emit garbage. Clamp duration so a missing end can't
                    # produce a negative value.
                    if start_ns <= 0:
                        logger.warning(
                            "Dropping span %s: missing/invalid startTimeUnixNano",
                            span.get("spanId", ""),
                        )
                        continue
                    duration_ms = (
                        max(0.0, (end_ns - start_ns) / 1_000_000) if end_ns > 0 else 0.0
                    )

                    spans.append(TraceSpan(
                        span_id=span.get("spanId", ""),
                        parent_span_id=span.get("parentSpanId") or None,
                        service=service,
                        operation=span.get("name", "unknown"),
                        duration_ms=duration_ms,
                        status_code=status_code,
                        error_message=error_msg,
                        # Aware UTC — naive datetimes break S3 ordering + windowing.
                        start_time=datetime.fromtimestamp(
                            start_ns / 1_000_000_000, tz=timezone.utc
                        ),
                        kind=SpanKind.from_otlp(span.get("kind", 0)),
                        attributes=_flatten_attributes(span),
                    ))
        logger.debug("Parsed %d spans", len(spans))
        return spans
