"""C1 — Telemetry adapters (A → B, A → C).

Dev C uses logs(node, window) for failure classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class LogRecord:
    timestamp: datetime
    node_id: str
    severity: str
    message: str
    attributes: dict[str, str] | None = None


class TelemetryAdapters(Protocol):
    """Read API over the OTel stack. A implements; B/C consume."""

    def logs(self, node_id: str, window_start: datetime, window_end: datetime) -> list[LogRecord]:
        """Return log records for node in [window_start, window_end]."""
        ...
