"""C4 — Scorer port helpers (weights / enabled signals / Protocol).

Dev B implements scoring.scorer.Scorer; Dev C drives ablation via this port.
stdlib-only — no imports from scoring/ or graphs/.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from .rca import RankedResult


@dataclass(frozen=True)
class Weights:
    w_T: float = 1.0 / 3.0
    w_D: float = 1.0 / 3.0
    w_P: float = 1.0 / 3.0

    def as_dict(self) -> dict[str, float]:
        return {"w_T": self.w_T, "w_D": self.w_D, "w_P": self.w_P}

    def to_signal_dict(self) -> dict[str, float]:
        return {"T": self.w_T, "D": self.w_D, "P": self.w_P}

    @classmethod
    def from_signal_dict(cls, d: dict[str, float] | None) -> Weights:
        if not d:
            return cls()
        return cls(
            w_T=float(d.get("T", d.get("w_T", 1.0 / 3.0))),
            w_D=float(d.get("D", d.get("w_D", 1.0 / 3.0))),
            w_P=float(d.get("P", d.get("w_P", 1.0 / 3.0))),
        )


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

    def to_set(self) -> set[str]:
        return {n for n, on in (("T", self.T), ("D", self.D), ("P", self.P)) if on}

    @classmethod
    def from_iterable(cls, names: Iterable[str] | None) -> EnabledSignals:
        if names is None:
            return cls.all()
        return cls.only(*set(names))


class Scorer(Protocol):
    def score(
        self,
        incident_subgraph: Any,
        weights: Weights | dict[str, float] | None = None,
        enabled_signals: EnabledSignals | Iterable[str] | None = None,
    ) -> RankedResult:
        ...


def normalize_weights(weights: Weights | dict[str, float] | None) -> Weights:
    if weights is None:
        return Weights()
    if isinstance(weights, Weights):
        return weights
    return Weights.from_signal_dict(weights)


def normalize_enabled(
    enabled: EnabledSignals | Iterable[str] | None,
) -> EnabledSignals:
    if enabled is None:
        return EnabledSignals.all()
    if isinstance(enabled, EnabledSignals):
        return enabled
    return EnabledSignals.from_iterable(enabled)
