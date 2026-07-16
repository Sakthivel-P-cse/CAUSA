"""Per-class confusion matrix vs chaos fault_class ground truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from contracts.c3_candidates import FaultClass


CLASSES = list(FaultClass)


@dataclass
class ConfusionMatrix:
    labels: list[FaultClass] = field(default_factory=lambda: list(FaultClass))
    matrix: dict[str, dict[str, int]] = field(default_factory=dict)
    n: int = 0

    def __post_init__(self) -> None:
        if not self.matrix:
            self.matrix = {
                a.value: {b.value: 0 for b in self.labels} for a in self.labels
            }

    def add(self, truth: FaultClass, predicted: FaultClass) -> None:
        self.matrix[truth.value][predicted.value] += 1
        self.n += 1

    @property
    def accuracy(self) -> float:
        if self.n == 0:
            return 0.0
        correct = sum(self.matrix[c.value][c.value] for c in self.labels)
        return correct / self.n

    def hard_confusions(self) -> list[tuple[str, str, int]]:
        """Surface Dependency↔Resource and Config↔AppLogic."""
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
    pairs: Sequence[tuple[FaultClass, FaultClass]],
) -> ConfusionMatrix:
    """pairs: (truth, predicted)."""
    cm = ConfusionMatrix()
    for truth, pred in pairs:
        cm.add(truth, pred)
    return cm
