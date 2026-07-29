"""Independent safety check for the fitted confidence calibration artifact."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

_DEFAULT_ARTIFACT = (
    Path(__file__).resolve().parents[1] / "eval" / "artifacts" / "fit_result.json"
)


def automation_calibrated(path: Path | None = None) -> bool:
    artifact = path or Path(
        os.getenv("RCA_WEIGHTS_ARTIFACT", str(_DEFAULT_ARTIFACT))
    )
    try:
        raw = json.loads(artifact.read_text(encoding="utf-8"))
        if raw.get("release_ready") is not True:
            return False
        if raw.get("split_version") != "rca-stratified-v1":
            return False
        digest = str(raw.get("dataset_sha256") or "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            return False
        mapping = (raw.get("calibration") or {}).get("mapping")
        calibration_n = int((raw.get("calibration") or {}).get("n") or raw.get("n") or 0)
        holdout_n = int(
            ((raw.get("holdout") or {}).get("ranking") or {}).get("n") or 0
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    minimum = int(os.getenv("CAUSA_AUTO_REMEDIATION_MIN_CALIBRATION_N", "30"))
    holdout_minimum = int(os.getenv("CAUSA_AUTO_REMEDIATION_MIN_HOLDOUT_N", "10"))
    if (
        not isinstance(mapping, list)
        or len(mapping) < 2
        or calibration_n < minimum
        or holdout_n < holdout_minimum
    ):
        return False
    values: list[float] = []
    for item in mapping:
        try:
            value = float(
                item[1]
                if isinstance(item, (list, tuple))
                else item["calibrated"]
            )
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        if not math.isfinite(value):
            return False
        values.append(value)
    return max(values) - min(values) > 1e-6
