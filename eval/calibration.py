"""Confidence calibration — reliability diagram + ECE (spec §5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from contracts import RankedResult, RootCauseCandidate


EPS = 1e-9


def raw_confidence(candidates: Sequence[RootCauseCandidate]) -> float:
    """raw_conf = S(top)·(S(top)−S(second))/(S(top)+ε)"""
    if not candidates:
        return 0.0
    top = candidates[0].score
    second = candidates[1].score if len(candidates) > 1 else 0.0
    return top * (top - second) / (top + EPS)


@dataclass
class CalibrationBin:
    lo: float
    hi: float
    mean_predicted: float
    empirical_accuracy: float
    count: int


@dataclass
class CalibrationResult:
    bins: list[CalibrationBin]
    ece: float
    map_kind: str
    # piecewise isotonic: list of (raw_conf threshold, calibrated prob)
    mapping: list[tuple[float, float]] = field(default_factory=list)
    reliability_points: list[tuple[float, float]] = field(default_factory=list)

    def apply(self, raw: float) -> float:
        if not self.mapping:
            return raw
        # Step / linear interp on isotonic map
        calibrated = self.mapping[0][1]
        for x, y in self.mapping:
            if raw >= x:
                calibrated = y
            else:
                break
        return calibrated

    def to_dict(self) -> dict:
        return {
            "ece": self.ece,
            "map_kind": self.map_kind,
            "bins": [
                {
                    "lo": b.lo,
                    "hi": b.hi,
                    "mean_predicted": b.mean_predicted,
                    "empirical_accuracy": b.empirical_accuracy,
                    "count": b.count,
                }
                for b in self.bins
            ],
            "reliability_points": [
                {"predicted": p, "empirical": e} for p, e in self.reliability_points
            ],
            "mapping": [{"raw": x, "calibrated": y} for x, y in self.mapping],
        }


def _isotonic_fit(pairs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Simple PAV-style isotonic regression on (raw_conf, correct∈{0,1})."""
    if not pairs:
        return [(0.0, 0.0), (1.0, 1.0)]
    pairs = sorted(pairs, key=lambda x: x[0])
    # Pool adjacent violators on means
    blocks: list[list[tuple[float, float]]] = [[pairs[0]]]
    for p in pairs[1:]:
        blocks.append([p])
        while len(blocks) >= 2:
            mean_prev = sum(y for _, y in blocks[-2]) / len(blocks[-2])
            mean_cur = sum(y for _, y in blocks[-1]) / len(blocks[-1])
            if mean_prev <= mean_cur:
                break
            blocks[-2].extend(blocks[-1])
            blocks.pop()
    mapping: list[tuple[float, float]] = []
    for block in blocks:
        xs = [x for x, _ in block]
        ys = [y for _, y in block]
        mapping.append((min(xs), sum(ys) / len(ys)))
    return mapping


def fit_calibration(
    train_results: Sequence[tuple[RankedResult, str]],
    n_bins: int = 10,
) -> CalibrationResult:
    """Fit isotonic map on train: (result, true_root_node)."""
    pairs: list[tuple[float, float]] = []
    for result, true_root in train_results:
        cands = result.primary_candidates
        raw = raw_confidence(cands)
        correct = 1.0 if cands and cands[0].node_id == true_root else 0.0
        pairs.append((raw, correct))

    mapping = _isotonic_fit(pairs)
    # Temporary result to apply
    tmp = CalibrationResult(bins=[], ece=0.0, map_kind="isotonic", mapping=mapping)
    return evaluate_calibration(train_results, tmp, n_bins=n_bins)


def evaluate_calibration(
    results: Sequence[tuple[RankedResult, str]],
    fitted: CalibrationResult,
    n_bins: int = 10,
) -> CalibrationResult:
    """Apply fitted map on a set; compute reliability bins + ECE."""
    preds: list[float] = []
    labels: list[float] = []
    for result, true_root in results:
        cands = result.primary_candidates
        raw = raw_confidence(cands)
        cal = fitted.apply(raw)
        preds.append(cal)
        labels.append(1.0 if cands and cands[0].node_id == true_root else 0.0)

    bins: list[CalibrationBin] = []
    ece = 0.0
    n = len(preds) or 1
    for i in range(n_bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        idx = [j for j, p in enumerate(preds) if (p >= lo and (p < hi or (i == n_bins - 1 and p <= hi)))]
        if not idx:
            continue
        mean_p = sum(preds[j] for j in idx) / len(idx)
        mean_y = sum(labels[j] for j in idx) / len(idx)
        bins.append(
            CalibrationBin(
                lo=lo,
                hi=hi,
                mean_predicted=mean_p,
                empirical_accuracy=mean_y,
                count=len(idx),
            )
        )
        ece += (len(idx) / n) * abs(mean_p - mean_y)

    points = [(b.mean_predicted, b.empirical_accuracy) for b in bins]
    return CalibrationResult(
        bins=bins,
        ece=ece,
        map_kind=fitted.map_kind,
        mapping=list(fitted.mapping),
        reliability_points=points,
    )


def apply_calibrated_confidence(
    result: RankedResult,
    calibration: CalibrationResult,
) -> RankedResult:
    """Write calibrated confidence into candidates (top uses map; others scaled)."""
    cands = result.primary_candidates
    if not cands:
        return result
    raw = raw_confidence(cands)
    top_conf = calibration.apply(raw)
    for i, c in enumerate(cands):
        if i == 0:
            c.confidence = top_conf
        else:
            # Monotone decay by rank for display
            c.confidence = max(0.0, top_conf * (c.score / (cands[0].score + EPS)))
    return result
