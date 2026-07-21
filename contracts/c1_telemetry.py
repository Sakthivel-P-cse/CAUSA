"""C1 — Telemetry read seam (A produces; B/C consume).

Dev C only needs logs for fault classification. Full span/metric/topology
adapters belong to Dev A; this module defines the narrow protocol C depends on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class LogRecord:
    timestamp: datetime
    node_id: str
    severity: str
    message: str
    attributes: dict[str, str] = field(default_factory=dict)


class LogsPort(Protocol):
    """Narrow C1 port: logs for one node in a time window."""

    def logs(
        self,
        node_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[LogRecord]:
        ...


TelemetryAdapters = LogsPort
