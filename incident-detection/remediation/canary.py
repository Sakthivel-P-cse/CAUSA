"""Simulated progressive canary with SLO watch + auto-rollback (spec §6.2 / PRD §8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence


@dataclass
class SLOSnapshot:
    step: int
    traffic_pct: float
    error_rate: float
    latency_p99_ms: float
    breached: bool


@dataclass
class CanaryResult:
    applied: bool
    rolled_back: bool
    final_traffic_pct: float
    snapshots: list[SLOSnapshot] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "applied": self.applied,
            "rolled_back": self.rolled_back,
            "final_traffic_pct": self.final_traffic_pct,
            "reason": self.reason,
            "snapshots": [
                {
                    "step": s.step,
                    "traffic_pct": s.traffic_pct,
                    "error_rate": s.error_rate,
                    "latency_p99_ms": s.latency_p99_ms,
                    "breached": s.breached,
                }
                for s in self.snapshots
            ],
        }


class SimulatedCanary:
    """Thin progressive rollout simulation — not a real Argo Rollouts controller."""

    def __init__(
        self,
        steps: Sequence[float] | None = None,
        max_error_rate: float = 0.05,
        max_latency_p99_ms: float = 500.0,
        metric_fn: Callable[[float], tuple[float, float]] | None = None,
    ):
        self.steps = list(steps or (10.0, 25.0, 50.0, 100.0))
        self.max_error_rate = max_error_rate
        self.max_latency_p99_ms = max_latency_p99_ms
        self.metric_fn = metric_fn or self._healthy_metrics

    @staticmethod
    def _healthy_metrics(traffic_pct: float) -> tuple[float, float]:
        return (0.01, 120.0)

    @staticmethod
    def breaching_metrics(traffic_pct: float) -> tuple[float, float]:
        if traffic_pct > 25:
            return (0.20, 900.0)
        return (0.02, 150.0)

    def apply(self) -> CanaryResult:
        snapshots: list[SLOSnapshot] = []
        for i, pct in enumerate(self.steps):
            err, lat = self.metric_fn(pct)
            breached = err > self.max_error_rate or lat > self.max_latency_p99_ms
            snapshots.append(
                SLOSnapshot(
                    step=i,
                    traffic_pct=pct,
                    error_rate=err,
                    latency_p99_ms=lat,
                    breached=breached,
                )
            )
            if breached:
                return CanaryResult(
                    applied=False,
                    rolled_back=True,
                    final_traffic_pct=0.0,
                    snapshots=snapshots,
                    reason=(
                        f"SLO breach at {pct}% traffic "
                        f"(err={err:.3f}, p99={lat:.1f}ms); auto-rollback"
                    ),
                )
        return CanaryResult(
            applied=True,
            rolled_back=False,
            final_traffic_pct=100.0,
            snapshots=snapshots,
            reason="Canary completed; SLOs healthy",
        )
