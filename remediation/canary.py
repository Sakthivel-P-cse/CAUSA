"""Deprecated simulated canary — runtime must use Argo + Prometheus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class CanaryResult:
    applied: bool = False
    rolled_back: bool = False
    snapshots: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "rolled_back": self.rolled_back,
            "snapshots": self.snapshots or [],
        }


@dataclass
class SLOSnapshot:
    error_rate: float
    latency_p99_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {"error_rate": self.error_rate, "latency_p99_ms": self.latency_p99_ms}


class SimulatedCanary:
    """Removed from production paths. Raises if invoked outside unit tests."""

    STEPS = (10, 25, 50, 100)

    def __init__(self, metric_fn: Callable[[int], tuple[float, float]] | None = None) -> None:
        self.metric_fn = metric_fn

    @staticmethod
    def breaching_metrics(step: int) -> tuple[float, float]:
        if step >= 25:
            return 0.20, 900.0
        return 0.01, 120.0

    def apply(self) -> CanaryResult:
        raise RuntimeError(
            "SimulatedCanary is disabled; use Argo Rollouts AnalysisRuns and remediation.slo"
        )
