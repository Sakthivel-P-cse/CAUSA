"""Fitted scoring defaults from C live eval (eval/fit_from_live artifacts).

Equal thirds remain the boot defaults until a live fit writes a non-degenerate
`eval/artifacts/fit_result.json` (or `RCA_WEIGHTS_ARTIFACT`). Degenerate fits
(zeroed components, single-signal mass) are rejected so production never
silently collapses to e.g. w_P=1.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path

from contracts import Weights

logger = logging.getLogger(__name__)

_DEFAULT = Weights(w_T=1.0 / 3.0, w_D=1.0 / 3.0, w_P=1.0 / 3.0)
_ARTIFACT = Path(__file__).resolve().parents[1] / "eval" / "artifacts" / "fit_result.json"
_WEIGHT_SUM_TOL = 1e-3
_MIN_CAL_POINTS = 10
_CAL_CONSTANT_EPS = 1e-6


def _artifact_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    env = os.getenv("RCA_WEIGHTS_ARTIFACT", "").strip()
    if env:
        return Path(env)
    return _ARTIFACT


def _read_artifact(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except Exception as exc:
        logger.warning("failed reading fit artifact %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("fit artifact %s is not a JSON object", path)
        return None
    return raw


def _parse_weights(bw: dict) -> Weights | None:
    try:
        w_T = float(bw["w_T"])
        w_D = float(bw["w_D"])
        w_P = float(bw["w_P"])
    except (KeyError, TypeError, ValueError):
        return None
    vals = (w_T, w_D, w_P)
    if not all(math.isfinite(v) and v > 0.0 for v in vals):
        return None
    total = w_T + w_D + w_P
    if total <= 0.0 or not math.isfinite(total):
        return None
    w_T, w_D, w_P = w_T / total, w_D / total, w_P / total
    if abs((w_T + w_D + w_P) - 1.0) > _WEIGHT_SUM_TOL:
        return None
    return Weights(w_T=w_T, w_D=w_D, w_P=w_P)


def load_fitted_weights(path: Path | None = None) -> Weights:
    p = _artifact_path(path)
    raw = _read_artifact(p)
    if raw is None:
        return _DEFAULT
    bw = raw.get("best_weights") or raw.get("recommended_weights") or {}
    if not isinstance(bw, dict):
        logger.warning("fitted weights missing in %s; using equal thirds", p)
        return _DEFAULT
    parsed = _parse_weights(bw)
    if parsed is None:
        logger.warning(
            "rejecting degenerate fitted weights from %s (%s); using equal thirds",
            p,
            bw,
        )
        return _DEFAULT
    return parsed


def _parse_calibration_mapping(raw: dict) -> list[tuple[float, float]] | None:
    cal = raw.get("calibration")
    if not isinstance(cal, dict):
        return None
    mapping_raw = cal.get("mapping")
    if not isinstance(mapping_raw, list) or len(mapping_raw) < _MIN_CAL_POINTS:
        return None
    mapping: list[tuple[float, float]] = []
    for item in mapping_raw:
        try:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                x, y = float(item[0]), float(item[1])
            elif isinstance(item, dict):
                x, y = float(item["raw"]), float(item["calibrated"])
            else:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        mapping.append((x, y))
    if len(mapping) < _MIN_CAL_POINTS:
        return None
    calibrated = [y for _, y in mapping]
    if max(calibrated) - min(calibrated) <= _CAL_CONSTANT_EPS:
        return None
    mapping.sort(key=lambda pair: pair[0])
    return mapping


def load_calibration_map(path: Path | None = None) -> list[tuple[float, float]] | None:
    """Return isotonic (raw, calibrated) pairs, or None when unsafe/missing."""
    p = _artifact_path(path)
    raw = _read_artifact(p)
    if raw is None:
        return None
    mapping = _parse_calibration_mapping(raw)
    if mapping is None:
        return None
    return mapping


def apply_calibration_map(raw: float, mapping: list[tuple[float, float]]) -> float:
    if not mapping:
        return raw
    calibrated = mapping[0][1]
    for x, y in mapping:
        if raw >= x:
            calibrated = y
        else:
            break
    return calibrated
