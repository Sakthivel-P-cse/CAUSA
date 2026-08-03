"""NULL rate + accuracy of rca_reports.failure_type vs GT fault_class."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from contracts import CLASSIFICATION_TARGETS, FaultClass
from classify.confusion import ConfusionMatrix


STRUCTURAL_CLASSES = frozenset(
    {FaultClass.INFRA, FaultClass.DEPENDENCY, FaultClass.RESOURCE}
)


def _parse_fc(value: Any) -> FaultClass | None:
    if value is None or value == "":
        return None
    if isinstance(value, FaultClass):
        return None if value is FaultClass.TIMING else value
    try:
        fc = FaultClass(value)
    except ValueError:
        return None
    return None if fc is FaultClass.TIMING else fc


def evaluate_failure_types(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Score reported failure_type against ground-truth fault_class.

    NULL rate is over the full corpus. Accuracy / confusion cover rows where
    both sides resolve to a classification target (TIMING excluded). Structural
    accuracy further restricts GT to Infra|Dependency|Resource.
    """
    n = len(rows)
    nulls = 0
    cm = ConfusionMatrix()
    structural_n = 0
    structural_hits = 0

    for row in rows:
        pred_raw = row.get("failure_type")
        truth_raw = row.get("fault_class")
        if pred_raw is None or pred_raw == "":
            nulls += 1

        truth = _parse_fc(truth_raw)
        pred = _parse_fc(pred_raw)

        if truth is not None and pred is not None:
            cm.add(truth, pred)

        if truth is not None and truth in STRUCTURAL_CLASSES:
            structural_n += 1
            if pred is not None and pred == truth:
                structural_hits += 1

    return {
        "n": n,
        "null_count": nulls,
        "null_rate": nulls / n if n else 0.0,
        "classified_n": cm.n,
        "match_rate": cm.accuracy,
        "structural_n": structural_n,
        "structural_accuracy": (
            structural_hits / structural_n if structural_n else 0.0
        ),
        "confusion": cm.to_dict(),
        "targets": [fc.value for fc in CLASSIFICATION_TARGETS],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Evaluate failure_type vs GT fault_class")
    p.add_argument("--dsn", default=None)
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="optional JSON output path",
    )
    args = p.parse_args(argv)

    from eval.live_store import LiveEvalStore

    try:
        rows = LiveEvalStore(dsn=args.dsn).join_reports_groundtruth()
    except Exception as exc:
        print(f"DB error: {exc}", file=sys.stderr)
        return 1

    result = evaluate_failure_types(rows)
    print(json.dumps(result, indent=2))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
