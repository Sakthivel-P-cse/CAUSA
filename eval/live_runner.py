"""Live corpus eval: join rca_reports × ground_truth → ranking + class metrics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from classify.failure_type_eval import evaluate_failure_types
from eval.metrics import compute_metrics


def ranked_ids_from_candidates(origin_candidates: Any) -> list[str]:
    if not origin_candidates:
        return []
    if isinstance(origin_candidates, (str, bytes, bytearray)):
        origin_candidates = json.loads(origin_candidates)
    if not isinstance(origin_candidates, list):
        return []
    ids: list[str] = []
    for c in origin_candidates:
        if isinstance(c, dict) and c.get("node_id"):
            ids.append(str(c["node_id"]))
        elif isinstance(c, str):
            ids.append(c)
    return ids


def _try_classifier_eval(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        from classify.classifier import classify_candidate
        from classify.confusion import ConfusionMatrix
        from contracts import RootCauseCandidate
    except Exception:
        return None

    cm = ConfusionMatrix()
    attempted = 0
    for row in rows:
        truth = row.get("fault_class")
        cands = row.get("origin_candidates") or []
        if not cands or not isinstance(cands, list):
            continue
        top = cands[0]
        if not isinstance(top, dict):
            continue
        try:
            candidate = RootCauseCandidate.from_dict(top)
        except Exception:
            continue
        attempted += 1
        pred = classify_candidate(candidate).fault_class
        if truth is not None:
            cm.add(truth, pred)
    if attempted == 0:
        return {"attempted": 0, "available": True}
    return {
        "available": True,
        "attempted": attempted,
        "n": cm.n,
        "accuracy": cm.accuracy,
        "confusion": cm.to_dict(),
    }


def evaluate_rows(
    rows: Sequence[dict[str, Any]],
    *,
    run_classifier: bool = True,
) -> dict[str, Any]:
    metric_cases: list[tuple[str, list[str], list[str], str | None]] = []
    for row in rows:
        true_root = row.get("true_root_node")
        if not true_root:
            continue
        ranked = ranked_ids_from_candidates(row.get("origin_candidates"))
        metric_cases.append((str(true_root), ranked, ranked, None))

    metrics = compute_metrics(metric_cases)
    failure = evaluate_failure_types(rows)
    out: dict[str, Any] = {
        "n": len(rows),
        "scored": metrics.n,
        "metrics": metrics.to_dict(),
        "failure_type": failure,
    }
    if run_classifier:
        clf = _try_classifier_eval(rows)
        if clf is not None:
            out["classifier"] = clf
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Live causa_eval ranking + class eval")
    p.add_argument("--dsn", default=None, help="override CAUSA_EVAL_DSN / PG*")
    p.add_argument(
        "--out",
        type=Path,
        default=Path("eval/artifacts/live_eval.json"),
    )
    p.add_argument(
        "--no-classifier",
        action="store_true",
        help="skip optional classify.classifier pass",
    )
    args = p.parse_args(argv)

    from eval.live_store import LiveEvalStore

    try:
        rows = LiveEvalStore(dsn=args.dsn).join_reports_groundtruth()
    except Exception as exc:
        print(f"DB error: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("warning: empty live corpus (no joined rca_reports × ground_truth rows)")

    result = evaluate_rows(rows, run_classifier=not args.no_classifier)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")

    m = result["metrics"]
    ft = result["failure_type"]
    print(
        f"live_eval n={result['n']} scored={result['scored']} "
        f"top1={m['top1']:.3f} top3={m['top3']:.3f} mrr={m['mrr']:.3f} "
        f"failure_type_null_rate={ft['null_rate']:.3f} "
        f"failure_type_match={ft['match_rate']:.3f}"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
