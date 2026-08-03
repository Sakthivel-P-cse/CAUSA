"""Build a versioned RCA model release from operator-confirmed labels.

The command fails closed when coverage is insufficient. It never treats
unlabelled reports, fixture incidents, or a training-set calibration score as
production evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from contracts import RankedResult, RootCauseCandidate, Weights
from eval.calibration import evaluate_calibration, fit_calibration
from eval.fit_from_live import _rerank, fit_from_rows
from eval.metrics import compute_metrics

SPLIT_VERSION = "rca-stratified-v1"


def _canonical(rows: Sequence[dict[str, Any]]) -> bytes:
    ordered = sorted(rows, key=lambda row: str(row.get("incident_id") or ""))
    return json.dumps(
        ordered, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def dataset_sha256(rows: Sequence[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical(rows)).hexdigest()


def _stratum(row: dict[str, Any]) -> str:
    fields = (
        row.get("fault_class") or "unknown-fault",
        row.get("alert_name") or "unknown-alert",
        row.get("topology_id") or "unknown-topology",
    )
    return "|".join(str(value) for value in fields)


def validate_rows(rows: Sequence[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        incident_id = str(row.get("incident_id") or "")
        if not incident_id:
            errors.append(f"row {index}: missing incident_id")
        elif incident_id in seen:
            errors.append(f"row {index}: duplicate incident_id {incident_id}")
        seen.add(incident_id)
        for field in ("true_root_node", "fault_class", "alert_name", "topology_id"):
            if not row.get(field):
                errors.append(f"{incident_id or index}: missing {field}")
        if not isinstance(row.get("origin_candidates"), list) or not row[
            "origin_candidates"
        ]:
            errors.append(f"{incident_id or index}: missing origin_candidates")
        if not row.get("labeler") or not row.get("verified_at"):
            errors.append(
                f"{incident_id or index}: label provenance requires labeler and verified_at"
            )
    return errors


def split_rows(
    rows: Sequence[dict[str, Any]],
    *,
    train_ratio: float = 0.6,
    calibration_ratio: float = 0.2,
    seed: str = "causa-rca-v1",
) -> dict[str, list[dict[str, Any]]]:
    if train_ratio <= 0 or calibration_ratio <= 0:
        raise ValueError("train and calibration ratios must be positive")
    if train_ratio + calibration_ratio >= 1:
        raise ValueError("a non-empty holdout ratio is required")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_stratum(row)].append(row)
    result = {"train": [], "calibration": [], "holdout": []}
    for key, group in sorted(grouped.items()):
        ranked = sorted(
            group,
            key=lambda row: hashlib.sha256(
                f"{seed}:{key}:{row['incident_id']}".encode()
            ).hexdigest(),
        )
        n = len(ranked)
        n_train = math.floor(n * train_ratio)
        n_cal = math.floor(n * calibration_ratio)
        result["train"].extend(ranked[:n_train])
        result["calibration"].extend(ranked[n_train : n_train + n_cal])
        result["holdout"].extend(ranked[n_train + n_cal :])
    return result


def coverage_audit(
    splits: dict[str, list[dict[str, Any]]],
    *,
    min_calibration_per_stratum: int = 30,
    min_holdout_per_stratum: int = 10,
) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"train": 0, "calibration": 0, "holdout": 0}
    )
    for split, rows in splits.items():
        for row in rows:
            counts[_stratum(row)][split] += 1
    failures: list[str] = []
    for key, value in sorted(counts.items()):
        if value["calibration"] < min_calibration_per_stratum:
            failures.append(
                f"{key}: calibration n={value['calibration']} "
                f"< {min_calibration_per_stratum}"
            )
        if value["holdout"] < min_holdout_per_stratum:
            failures.append(
                f"{key}: holdout n={value['holdout']} < {min_holdout_per_stratum}"
            )
    return {"counts": dict(counts), "failures": failures, "passed": not failures}


def _ranked(
    row: dict[str, Any],
    weights: Weights,
) -> tuple[RankedResult, str] | None:
    true_root = str(row.get("true_root_node") or "")
    raw = row.get("origin_candidates") or []
    by_id = {
        str(candidate.get("node_id")): candidate
        for candidate in raw
        if isinstance(candidate, dict) and candidate.get("node_id")
    }
    parsed: list[RootCauseCandidate] = []
    for node_id in _rerank(list(by_id.values()), weights):
        try:
            candidate = RootCauseCandidate.from_dict(by_id[node_id])
        except (KeyError, TypeError, ValueError):
            continue
        signals = candidate.signals
        candidate.score = (
            weights.w_T * signals.T
            + weights.w_D * signals.D
            + weights.w_P * signals.P
        )
        parsed.append(candidate)
    if not true_root or not parsed:
        return None
    return (
        RankedResult(candidates=parsed, incident_id=str(row["incident_id"])),
        true_root,
    )


def build_release(
    rows: Sequence[dict[str, Any]],
    *,
    min_calibration_per_stratum: int = 30,
    min_holdout_per_stratum: int = 10,
) -> dict[str, Any]:
    errors = validate_rows(rows)
    splits = split_rows(rows)
    audit = coverage_audit(
        splits,
        min_calibration_per_stratum=min_calibration_per_stratum,
        min_holdout_per_stratum=min_holdout_per_stratum,
    )
    if errors or not audit["passed"]:
        return {
            "release_ready": False,
            "dataset_sha256": dataset_sha256(rows),
            "n": len(rows),
            "validation_errors": errors,
            "coverage": audit,
        }

    fitted = fit_from_rows(splits["train"])
    weights = Weights(**fitted["best_weights"])
    calibration_cases = [
        case for row in splits["calibration"] if (case := _ranked(row, weights))
    ]
    holdout_cases = [
        case for row in splits["holdout"] if (case := _ranked(row, weights))
    ]
    calibration = fit_calibration(calibration_cases)
    holdout_calibration = evaluate_calibration(holdout_cases, calibration)
    ranking = compute_metrics(
        [
            (
                truth,
                result.ranked_node_ids(),
                [candidate.node_id for candidate in result.candidates],
                _stratum(row),
            )
            for row in splits["holdout"]
            if (case := _ranked(row, weights))
            for result, truth in [case]
        ]
    )
    calibration_dict = calibration.to_dict()
    calibration_dict["n"] = len(calibration_cases)
    calibrated_values = [
        float(item["calibrated"]) for item in calibration_dict["mapping"]
    ]
    calibration_usable = (
        len(calibrated_values) >= 2
        and max(calibrated_values) - min(calibrated_values) > 1e-6
    )
    return {
        "release_ready": calibration_usable,
        "release_blockers": (
            []
            if calibration_usable
            else ["calibration mapping is constant or has fewer than two support points"]
        ),
        "split_version": SPLIT_VERSION,
        "dataset_sha256": dataset_sha256(rows),
        "n": len(rows),
        "split_counts": {key: len(value) for key, value in splits.items()},
        "split_incident_ids": {
            key: [str(row["incident_id"]) for row in value]
            for key, value in splits.items()
        },
        "coverage": audit,
        "best_weights": fitted["best_weights"],
        "training_top1": fitted.get("best_top1"),
        "calibration": calibration_dict,
        "holdout": {
            "ranking": ranking.to_dict(),
            "calibration_ece": holdout_calibration.ece,
            "calibration_bins": holdout_calibration.to_dict()["bins"],
            "abstention_precision": None,
            "abstention_note": (
                "not reported until abstention decisions and adjudicated "
                "abstention labels are stored in the corpus"
            ),
        },
    }


def _load(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    value = json.loads(text)
    if not isinstance(value, list):
        raise ValueError("corpus JSON must be a list")
    return [row for row in value if isinstance(row, dict)]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a gated RCA model release")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--min-calibration-per-stratum", type=int, default=30)
    parser.add_argument("--min-holdout-per-stratum", type=int, default=10)
    args = parser.parse_args(list(argv) if argv is not None else None)
    rows = _load(args.input)
    release = build_release(
        rows,
        min_calibration_per_stratum=args.min_calibration_per_stratum,
        min_holdout_per_stratum=args.min_holdout_per_stratum,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"release_ready": release["release_ready"], "n": len(rows)}))
    return 0 if release["release_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
