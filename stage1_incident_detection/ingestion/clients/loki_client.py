"""
loki_client.py — Query Loki for logs.

Talks to: loki.loki.svc.cluster.local:3100
Feeds:    LogRecordEnvelope / error-evidence collection.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_NS_PER_SEC = 1_000_000_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _service_name_label_keys() -> list[str]:
    keys_env = os.getenv(
        "CAUSA_SERVICE_NAME_LABEL_KEYS",
        "service,app,app_kubernetes_io_name,k8s_app,workload,deployment,container",
    )
    return [k.strip() for k in keys_env.split(",") if k.strip()]


class LokiClient:
    """Thin wrapper around the Loki query HTTP API."""

    def __init__(self, base_url: str = "http://loki.loki.svc.cluster.local:3100"):
        self._base = base_url.rstrip("/")
        self._http = httpx.Client(
            timeout=15.0,
            transport=httpx.HTTPTransport(retries=3),
        )

    def close(self) -> None:
        self._http.close()

    # ── Raw query helpers ─────────────────────────────────────────────

    def _query_range(
        self,
        logql: str,
        start: datetime,
        end: datetime,
        limit: int = 200,
        direction: str = "backward",
    ) -> list[dict[str, Any]]:
        params = {
            "query": logql,
            "start": int(start.timestamp() * _NS_PER_SEC),
            "end": int(end.timestamp() * _NS_PER_SEC),
            "limit": limit,
            "direction": direction,
        }
        resp = self._http.get(f"{self._base}/loki/api/v1/query_range", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("result", [])

    def _query_range_first_available(
        self,
        logql_candidates: list[str],
        start: datetime,
        end: datetime,
        limit: int = 200,
        direction: str = "backward",
    ) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for logql in logql_candidates:
            try:
                results = self._query_range(logql, start, end, limit=limit, direction=direction)
            except Exception as exc:
                last_error = exc
                logger.warning("Loki query failed for %s: %s", logql, exc)
                continue
            if results:
                return results

        if last_error:
            logger.warning("All Loki query candidates failed or returned empty: %s", last_error)
        return []

    @staticmethod
    def _escape_logql_regex(value: str) -> str:
        return re.escape(value)

    @staticmethod
    def _service_from_labels(stream_labels: dict[str, Any]) -> str:
        pod = stream_labels.get("pod", "")
        job = stream_labels.get("job", "")
        for key in ("service", "service_name", "app", "app_kubernetes_io_name"):
            value = stream_labels.get(key)
            if value:
                return str(value)
        if job and not str(job).startswith("kubernetes-pods"):
            return str(job)
        if pod:
            parts = str(pod).rsplit("-", 2)
            return parts[0] if len(parts) >= 3 else str(pod)
        return "unknown"

    @staticmethod
    def _level_from_labels(stream_labels: dict[str, Any], line: str) -> str:
        for key in ("level", "severity", "severity_text", "lvl"):
            value = stream_labels.get(key)
            if value:
                return str(value).lower()

        lowered = line.lower()
        if " error " in f" {lowered} " or "exception" in lowered:
            return "error"
        if " warn " in f" {lowered} " or "warning" in lowered:
            return "warn"
        if " debug " in f" {lowered} ":
            return "debug"
        if " info " in f" {lowered} ":
            return "info"
        return "unknown"

    def _candidate_selectors(
        self,
        service: str | None = None,
        level: str | None = None,
    ) -> list[str]:
        selectors: list[str] = []

        label_keys = _service_name_label_keys()

        if service:
            service_regex = self._escape_logql_regex(service)
            service_matchers = [f'{key}="{service}"' for key in label_keys]
            # Always append the standard pod-name fallback
            service_matchers.append(f'pod=~"{service_regex}-.*"')
        else:
            service_matchers = [f'{key}=~".+"' for key in label_keys]
            service_matchers.append('pod=~".+"')

        level_keys = ["level", "severity", "severity_text", "lvl"] if level else [None]

        for matcher in service_matchers:
            for level_key in level_keys:
                if level_key:
                    selectors.append(f'{{{matcher}, {level_key}="{level}"}}')
                else:
                    selectors.append(f'{{{matcher}}}')

        return selectors

    # ── Log explorer ──────────────────────────────────────────────────

    def get_logs(
        self,
        service: str | None = None,
        level: str | None = None,
        search: str | None = None,
        lookback_minutes: int = 30,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """
        Return recent log entries. Filters are ANDed together.

        Each entry is: { timestamp, service, level, message, labels }
        """
        end = _utcnow()
        start = end - timedelta(minutes=lookback_minutes)

        selectors = self._candidate_selectors(service=service, level=level)
        if search:
            logql_candidates = [f'{selector} |= "{search}"' for selector in selectors]
        else:
            logql_candidates = selectors

        results = self._query_range_first_available(logql_candidates, start, end, limit=limit)

        entries: list[dict[str, Any]] = []
        for stream in results:
            stream_labels = stream.get("stream", {})
            for ts_ns, line in stream.get("values", []):
                entries.append({
                    "timestamp": datetime.utcfromtimestamp(int(ts_ns) / _NS_PER_SEC).isoformat(),
                    "service": self._service_from_labels(stream_labels),
                    "level": self._level_from_labels(stream_labels, line),
                    "message": line,
                    "labels": stream_labels,
                })
        return entries

    # ── Error logs for a service around incident time ─────────────────

    def get_error_logs(
        self,
        service: str,
        around: datetime,
        window_minutes: int = 5,
        limit: int = 50,
    ) -> list[str]:
        """
        Return error log lines for a service in a window around `around`.
        Used to extract error messages as evidence.
        """
        start = around - timedelta(minutes=window_minutes)
        end = around + timedelta(minutes=window_minutes)

        selectors = self._candidate_selectors(service=service)
        logql_candidates = [
            f'{selector} |~ "(?i)(error|exception|fail|refused|timeout|oom)"'
            for selector in selectors
        ]
        results = self._query_range_first_available(logql_candidates, start, end, limit=limit)

        lines: list[str] = []
        for stream in results:
            for _, line in stream.get("values", []):
                lines.append(line)
        return lines
