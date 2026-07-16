"""C4 — Scorer interface (B implements; C drives tuning / ablation)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from contracts.c3_candidates import RankedResult


@dataclass(frozen=True)
class Weights:
    w_T: float = 1.0 / 3.0
    w_D: float = 1.0 / 3.0
    w_P: float = 1.0 / 3.0

    def as_dict(self) -> dict[str, float]:
        return {"w_T": self.w_T, "w_D": self.w_D, "w_P": self.w_P}


@dataclass(frozen=True)
class EnabledSignals:
    T: bool = True
    D: bool = True
    P: bool = True

    @classmethod
    def all(cls) -> EnabledSignals:
        return cls(True, True, True)

    @classmethod
    def only(cls, *names: str) -> EnabledSignals:
        flags = {n: False for n in ("T", "D", "P")}
        for n in names:
            flags[n] = True
        return cls(**flags)

    @classmethod
    def without(cls, name: str) -> EnabledSignals:
        flags = {"T": True, "D": True, "P": True}
        flags[name] = False
        return cls(**flags)


class Scorer(Protocol):
    """Dev B owns the implementation; Dev C calls this for ablation/tuning."""

    def score(
        self,
        incident_subgraph: Any,
        weights: Weights | None = None,
        enabled_signals: EnabledSignals | None = None,
    ) -> RankedResult:
        ...
