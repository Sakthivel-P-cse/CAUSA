from __future__ import annotations

from eval.corpus_pipeline import build_release, split_rows


def _candidate(node: str, score: float) -> dict:
    return {
        "node_id": node,
        "node_kind": "service",
        "score": score,
        "confidence": score,
        "role": "origin",
        "signals": {"T": score, "D": score, "P": score},
    }


def _row(index: int) -> dict:
    low_confidence_miss = index % 5 == 0
    return {
        "incident_id": f"inc-{index:04d}",
        "true_root_node": "frontend" if low_confidence_miss else "checkout",
        "fault_class": "AppLogic",
        "alert_name": "HighErrorRate",
        "topology_id": "otel-demo-v1",
        "labeler": "operator:test",
        "verified_at": "2026-07-28T00:00:00Z",
        "origin_candidates": [
            _candidate("checkout", 0.55 if low_confidence_miss else 0.9),
            _candidate("frontend", 0.5 if low_confidence_miss else 0.2),
        ],
    }


def test_splits_are_disjoint_and_deterministic():
    rows = [_row(index) for index in range(150)]
    first = split_rows(rows)
    second = split_rows(list(reversed(rows)))
    ids = {
        name: {row["incident_id"] for row in values}
        for name, values in first.items()
    }
    assert ids["train"].isdisjoint(ids["calibration"])
    assert ids["train"].isdisjoint(ids["holdout"])
    assert ids["calibration"].isdisjoint(ids["holdout"])
    assert {
        name: [row["incident_id"] for row in values]
        for name, values in first.items()
    } == {
        name: [row["incident_id"] for row in values]
        for name, values in second.items()
    }


def test_release_fails_closed_on_missing_coverage():
    release = build_release([_row(index) for index in range(20)])
    assert release["release_ready"] is False
    assert release["coverage"]["failures"]


def test_release_uses_disjoint_calibration_and_holdout():
    release = build_release([_row(index) for index in range(150)])
    assert release["release_ready"] is True
    assert release["split_counts"] == {
        "train": 90,
        "calibration": 30,
        "holdout": 30,
    }
    assert release["calibration"]["n"] == 30
    assert 0.0 < release["holdout"]["ranking"]["top1"] < 1.0
