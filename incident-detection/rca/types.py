"""Internal aggregate types consumed by the Dev B kernel."""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts import Thresholds


@dataclass(frozen=True)
class KernelAnomaly:
    anomaly_score: dict[str, float]
    first_deviation_time: dict[str, float | None]
    resource_deviation: dict[str, bool]
    anomalous_trace_ids: frozenset[str] = frozenset()
    t0: float = 0.0
    dev_vector: dict[str, dict[str, float]] = field(default_factory=dict)
    thresholds: Thresholds = field(default_factory=Thresholds)
