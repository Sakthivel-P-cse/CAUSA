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
    # Prefer `job`: OTel→Loki often promotes service.name into Loki's `job` label
    # (this demo stack has job=cart|… and no service/service_name stream labels).
    keys_env = os.getenv(
        "CAUSA_SERVICE_NAME_LABEL_KEYS",
        "job,service,service_name,app,app_kubernetes_io_name,k8s_app,workload,deployment,container",
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
    def _escape_logql_string(value: str) -> str:
        """
        Escape a value destined for a double-quoted LogQL string (an exact
        label matcher `key="..."` or a line filter). Without this a service or
        level containing `"` or `\\` breaks out of the matcher and the whole
        selector silently returns empty.
        """
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _level_line_regex(level: str) -> str:
        """
        A case-insensitive line-filter regex for a severity that lives INSIDE the
        log line rather than in a stream label. Mirrors get_error_logs' pattern
        for `error`; other levels match their own token. The alternation is
        embedded in a double-quoted LogQL string, so it is string-escaped.
        """
        lowered = level.strip().lower()
        groups = {
            "error": "error|exception|fail|refused|timeout|oom",
            "warn": "warn|warning",
            "warning": "warn|warning",
            "info": "info",
            "debug": "debug",
        }
        alt = groups.get(lowered) or re.escape(lowered)
        return LokiClient._escape_logql_string(f"(?i)({alt})")

    @staticmethod
    def _service_from_labels(stream_labels: dict[str, Any]) -> str:
        from ingest.service_scope import normalize_service_name

        pod = stream_labels.get("pod", "")
        job = stream_labels.get("job", "")
        raw = None
        for key in ("service", "service_name", "app", "app_kubernetes_io_name", "job"):
            value = stream_labels.get(key)
            if value:
                raw = str(value)
                break
        if raw is None and job and not str(job).startswith("kubernetes-pods"):
            raw = str(job)
        if raw is None and pod:
            parts = str(pod).rsplit("-", 2)
            raw = parts[0] if len(parts) >= 3 else str(pod)
        return normalize_service_name(raw) or "unknown"

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
            service_exact = self._escape_logql_string(service)
            # The pod fallback puts a REGEX inside a double-quoted LogQL string,
            # so escape twice: regex-escape metachars, then string-escape the
            # result's quotes/backslashes for the `"..."` literal.
            service_regex = self._escape_logql_string(self._escape_logql_regex(service))
            service_matchers = [f'{key}="{service_exact}"' for key in label_keys]
            # OTel→Loki often stores peers as job="unknown_service:<name>".
            unknown_exact = self._escape_logql_string(f"unknown_service:{service}")
            service_matchers.append(f'job="{unknown_exact}"')
            service_matchers.append(f'pod=~"{service_regex}-.*"')
        else:
            service_matchers = [f'{key}=~".+"' for key in label_keys]
            service_matchers.append('pod=~".+"')

        level_keys = ["level", "severity", "severity_text", "lvl"] if level else [None]
        level_exact = self._escape_logql_string(level) if level else ""
        level_regex = (
            self._escape_logql_string(self._escape_logql_regex(level.strip()))
            if level
            else ""
        )

        for matcher in service_matchers:
            for level_key in level_keys:
                if level_key:
                    # OTel often emits INFO/WARN uppercase; UI sends lowercase.
                    selectors.append(
                        f'{{{matcher}, {level_key}=~"(?i){level_regex}"}}'
                    )
                    selectors.append(f'{{{matcher}, {level_key}="{level_exact}"}}')
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
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return recent log entries. Filters are ANDed together.

        If `start`/`end` are given, the query honors that ABSOLUTE window (the
        C1 `logs(window)` adapter path); otherwise it looks back
        `lookback_minutes` from now (interactive/dashboard path).

        Each entry is: { timestamp, service, level, message, labels }
        """
        if start is None or end is None:
            end = _utcnow()
            start = end - timedelta(minutes=lookback_minutes)

        selectors = self._candidate_selectors(service=service, level=level)
        if search:
            # Escape quotes/backslashes so a search term can't break out of the
            # LogQL line filter (`|= "..."`).
            safe = self._escape_logql_string(search)
            logql_candidates = [f'{selector} |= "{safe}"' for selector in selectors]
        else:
            logql_candidates = list(selectors)
            # Fallback: on stacks where the level is NOT promoted to a stream
            # label, the label-matched selectors above all return empty. Append
            # line-filter candidates over service-only selectors so logs(level=…)
            # still finds them (audit M8). Tried only AFTER the label path, so a
            # label-bearing stack keeps its exact-match behavior.
            if level:
                base_selectors = self._candidate_selectors(service=service, level=None)
                line_re = self._level_line_regex(level)
                logql_candidates += [f'{sel} |~ "{line_re}"' for sel in base_selectors]

        results = self._query_range_first_available(logql_candidates, start, end, limit=limit)

        entries: list[dict[str, Any]] = []
        for stream in results:
            stream_labels = stream.get("stream", {})
            for ts_ns, line in stream.get("values", []):
                entries.append({
                    "timestamp": datetime.fromtimestamp(
                        int(ts_ns) / _NS_PER_SEC, tz=timezone.utc
                    ).isoformat(),
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
