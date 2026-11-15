"""Fit scoring defaults from the live causa_eval corpus.

Fitted weights should be applied to scoring defaults (contracts.Weights /
scoring signal mix). Live rows already carry per-candidate T/D/P in
origin_candidates.signals — when present we re-rank under a weight grid;
otherwise we recommend equal thirds and still emit calibration ECE from
confidence_score vs top-1 hit.
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import product
from pathlib import Path
from typing import Any, Sequence

from contracts import Weights
from eval.live_runner import ranked_ids_from_candidates
from eval.metrics import hit_at_k


MIN_ROWS = 3
_NOTE_EQUAL = (
    "recommended equal weights — live corpus too small or candidates lack "
    "re-scorable T/D/P signals; apply Weights() defaults until chaos corpus grows"
)
_NOTE_FITTED = (
    "best_weights from live re-rank grid on stored T/D/P signals; "
    "apply to scoring defaults (normalize_weights / Scorer weights)"
)


def _signals(c: dict) -> tuple[float, float, float] | None:
    sig = c.get("signals")
    if not isinstance(sig, dict):
        return None
    try:
        return float(sig.get("T", 0.0)), float(sig.get("D", 0.0)), float(sig.get("P", 0.0))
    except (TypeError, ValueError):
        return None


def _can_rescore(rows: Sequence[dict[str, Any]]) -> bool:
    for row in rows:
        for c in row.get("origin_candidates") or []:
            if isinstance(c, dict) and _signals(c) is not None:
                return True
    return False


def _rerank(cands: list[dict], w: Weights) -> list[str]:
    scored: list[tuple[float, str]] = []
    for c in cands:
        if not isinstance(c, dict) or not c.get("node_id"):
            continue
        sig = _signals(c)
        if sig is None:
            score = float(c.get("score") or 0.0)
        else:
            t, d, p = sig
            score = w.w_T * t + w.w_D * d + w.w_P * p
        scored.append((score, str(c["node_id"])))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [nid for _, nid in scored]


def _weight_grid(steps: Sequence[float] = (0.0, 0.25, 0.5, 0.75, 1.0)) -> list[Weights]:
    out: list[Weights] = []
    seen: set[tuple[float, float, float]] = set()
    for wt, wd, wp in product(steps, repeat=3):
        if wt + wd + wp <= 0:
            continue
        s = wt + wd + wp
        key = (round(wt / s, 4), round(wd / s, 4), round(wp / s, 4))
        if key in seen:
            continue
        seen.add(key)
        out.append(Weights(w_T=key[0], w_D=key[1], w_P=key[2]))
    return out


def top1_under_weights(rows: Sequence[dict[str, Any]], w: Weights) -> float:
    n = 0
    hits = 0
    for row in rows:
        true_root = row.get("true_root_node")
        cands = row.get("origin_candidates") or []
        if not true_root or not cands:
            continue
        ranked = _rerank(list(cands), w)
        n += 1
        if hit_at_k(str(true_root), ranked, 1):
            hits += 1
    return hits / n if n else 0.0


def _ece_stub(
    rows: Sequence[dict[str, Any]],
    n_bins: int = 10,
) -> dict[str, Any] | None:
    """ECE of confidence_score vs top-1 hit (identity map; no isotonic fit)."""
    preds: list[float] = []
    labels: list[float] = []
    for row in rows:
        true_root = row.get("true_root_node")
        conf = row.get("confidence_score")
        if true_root is None or conf is None:
            continue
        ranked = ranked_ids_from_candidates(row.get("origin_candidates"))
        preds.append(max(0.0, min(1.0, float(conf))))
        labels.append(1.0 if ranked and ranked[0] == str(true_root) else 0.0)

    if not preds:
        return None

    bins = []
    ece = 0.0
    n = len(preds)
    for i in range(n_bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        idx = [
            j
            for j, p in enumerate(preds)
            if p >= lo and (p < hi or (i == n_bins - 1 and p <= hi))
        ]
        if not idx:
            continue
        mean_p = sum(preds[j] for j in idx) / len(idx)
        mean_y = sum(labels[j] for j in idx) / len(idx)
        bins.append(
            {
                "lo": lo,
                "hi": hi,
                "mean_predicted": mean_p,
                "empirical_accuracy": mean_y,
                "count": len(idx),
            }
        )
        ece += (len(idx) / n) * abs(mean_p - mean_y)

    return {
        "ece": ece,
        "map_kind": "identity_stub",
        "n": n,
        "bins": bins,
        "note": "confidence_score vs top1 hit; fit_calibration needs RankedResult",
    }


def fit_from_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    equal = Weights()
    ece = _ece_stub(rows)
    result: dict[str, Any] = {
        "n": len(rows),
        "min_rows": MIN_ROWS,
        "best_weights": equal.as_dict(),
        "best_top1": None,
        "grid_size": 0,
        "note": _NOTE_EQUAL,
        "ece": ece,
    }

    if len(rows) < MIN_ROWS:
        result["note"] = (
            f"corpus n={len(rows)} < {MIN_ROWS}; " + _NOTE_EQUAL
        )
        return result

    if not _can_rescore(rows):
        result["best_top1"] = top1_under_weights(rows, equal)
        return result

    grid = _weight_grid()
    best_w = equal
    best_top1 = -1.0
    for w in grid:
        t1 = top1_under_weights(rows, w)
        if t1 > best_top1:
            best_top1 = t1
            best_w = w

    result.update(
        {
            "best_weights": best_w.as_dict(),
            "best_top1": best_top1,
            "equal_top1": top1_under_weights(rows, equal),
            "grid_size": len(grid),
            "note": _NOTE_FITTED,
        }
    )

    try:
        from contracts import RankedResult, RootCauseCandidate
        from eval.calibration import fit_calibration

        train: list[tuple[Any, str]] = []
        for row in rows:
            true_root = row.get("true_root_node")
            cands_raw = row.get("origin_candidates") or []
            if not true_root or not cands_raw:
                continue
            parsed = []
            for c in cands_raw:
                if not isinstance(c, dict):
                    continue
                try:
                    parsed.append(RootCauseCandidate.from_dict(c))
                except Exception:
                    continue
            if not parsed:
                continue
            train.append(
                (
                    RankedResult(
                        candidates=parsed,
                        incident_id=str(row.get("incident_id") or ""),
                    ),
                    str(true_root),
                )
            )
        if train:
            cal = fit_calibration(train)
            result["calibration"] = cal.to_dict()
            result["ece"] = {
                "ece": cal.ece,
                "map_kind": cal.map_kind,
                "n": len(train),
                "bins": cal.to_dict()["bins"],
                "note": "isotonic fit_calibration on reconstructed RankedResult",
            }
    except Exception as exc:
        result["calibration_error"] = str(exc)

    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Fit / recommend T/D/P weights from live causa_eval joins. "
            "Apply best_weights to scoring defaults."
        )
    )
    p.add_argument("--dsn", default=None)
    p.add_argument(
        "--out",
        type=Path,
        default=Path("eval/artifacts/fit_result.json"),
    )
    args = p.parse_args(argv)

    from eval.live_store import LiveEvalStore

    try:
        rows = LiveEvalStore(dsn=args.dsn).join_reports_groundtruth()
    except Exception as exc:
        print(f"DB error: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("warning: empty live corpus")

    result = fit_from_rows(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")

    bw = result["best_weights"]
    ece = (result.get("ece") or {}).get("ece")
    print(
        f"fit_from_live n={result['n']} "
        f"best_weights={bw} best_top1={result.get('best_top1')} "
        f"ece={ece} note={result['note']}"
    )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
