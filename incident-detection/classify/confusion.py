"""Per-class confusion matrix vs chaos fault_class ground truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from contracts import CLASSIFICATION_TARGETS, FaultClass


def _as_fault_class(value: FaultClass | str) -> FaultClass | None:
    if isinstance(value, FaultClass):
        return value if value is not FaultClass.TIMING else None
    try:
        fc = FaultClass(value)
    except ValueError:
        return None
    return None if fc is FaultClass.TIMING else fc


@dataclass
class ConfusionMatrix:
    labels: list[FaultClass] = field(default_factory=lambda: list(CLASSIFICATION_TARGETS))
    matrix: dict[str, dict[str, int]] = field(default_factory=dict)
    n: int = 0
    skipped_timing: int = 0

    def __post_init__(self) -> None:
        if not self.matrix:
            self.matrix = {
                a.value: {b.value: 0 for b in self.labels} for a in self.labels
            }

    def add(self, truth: FaultClass | str, predicted: FaultClass | str) -> None:
        t = _as_fault_class(truth)
        p = _as_fault_class(predicted)
        if t is None or p is None:
            self.skipped_timing += 1
            return
        self.matrix[t.value][p.value] += 1
        self.n += 1

    @property
    def accuracy(self) -> float:
        if self.n == 0:
            return 0.0
        correct = sum(self.matrix[c.value][c.value] for c in self.labels)
        return correct / self.n

    def hard_confusions(self) -> list[tuple[str, str, int]]:
        pairs = [
            (FaultClass.DEPENDENCY, FaultClass.RESOURCE),
            (FaultClass.RESOURCE, FaultClass.DEPENDENCY),
            (FaultClass.CONFIG, FaultClass.APP_LOGIC),
            (FaultClass.APP_LOGIC, FaultClass.CONFIG),
        ]
        out = []
        for a, b in pairs:
            count = self.matrix[a.value][b.value]
            if count:
                out.append((a.value, b.value, count))
        return out

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "accuracy": self.accuracy,
            "skipped_timing": self.skipped_timing,
            "matrix": self.matrix,
            "hard_confusions": [
                {"truth": a, "predicted": b, "count": c} for a, b, c in self.hard_confusions()
            ],
        }

    def render_table(self) -> str:
        header = "truth\\pred\t" + "\t".join(c.value for c in self.labels)
        rows = [header]
        for a in self.labels:
            cells = [str(self.matrix[a.value][b.value]) for b in self.labels]
            rows.append(a.value + "\t" + "\t".join(cells))
        rows.append(f"accuracy\t{self.accuracy:.3f}")
        return "\n".join(rows)


def score_classification(
    pairs: Sequence[tuple[FaultClass | str, FaultClass | str]],
) -> ConfusionMatrix:
    cm = ConfusionMatrix()
    for truth, pred in pairs:
        cm.add(truth, pred)
    return cm
