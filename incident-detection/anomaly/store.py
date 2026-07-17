"""
store.py — persist/load learned baselines.

A baseline is captured once per app-deployment on a healthy window and reused
across every incident, so it must survive process restarts and be shared between
the trigger service and the eval harness. JSON on disk is the portable default
(committable alongside a scenario run); an optional Postgres `anomaly_baseline`
table (see helm/postgres init.sql) can hold the same numbers for the cluster.

On-disk shape:
    { node: { metric: { "median": float, "mad": float, "n": int } } }
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from contracts import Baseline

logger = logging.getLogger(__name__)


def save_baselines(
    baselines: dict[tuple[str, str], Baseline], path: str | Path
) -> None:
    """Write baselines to JSON (nested node → metric → stats).

    `allow_nan=False`: rather than emit the non-standard `NaN`/`Infinity` tokens
    that strict JSON parsers and Postgres reject, a non-finite stat raises here
    (loud) — but robust_baseline already drops non-finite inputs, so this is a
    guard, not an expected path.
    """
    nested: dict[str, dict[str, dict[str, float | int]]] = {}
    for (node, metric), b in baselines.items():
        nested.setdefault(node, {})[metric] = {
            "median": b.median, "mad": b.mad, "n": b.n
        }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(nested, indent=2, sort_keys=True, allow_nan=False))


def load_baselines(path: str | Path) -> dict[tuple[str, str], Baseline]:
    """Load baselines from JSON into the {(node, metric): Baseline} map.

    Non-finite entries (from a legacy file that stored NaN/Infinity) are skipped
    with a warning rather than admitted into scoring.
    """
    nested = json.loads(Path(path).read_text())
    out: dict[tuple[str, str], Baseline] = {}
    for node, metrics in nested.items():
        for metric, stats in metrics.items():
            median = float(stats["median"])
            mad = float(stats["mad"])
            if not (math.isfinite(median) and math.isfinite(mad)):
                logger.warning(
                    "Skipping non-finite baseline for (%s, %s)", node, metric
                )
                continue
            out[(node, metric)] = Baseline(median=median, mad=mad, n=int(stats["n"]))
    return out
