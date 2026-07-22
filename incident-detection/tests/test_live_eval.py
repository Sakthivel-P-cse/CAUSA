"""Unit tests for live eval metrics path (mocked join rows, no PG)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from classify.failure_type_eval import evaluate_failure_types
from eval.fit_from_live import fit_from_rows
from eval.live_runner import evaluate_rows, ranked_ids_from_candidates


def _row(
    *,
    incident_id: str,
    true_root: str,
    ranked: list[str],
    failure_type: str | None,
    fault_class: str,
    confidence: float = 0.8,
    with_signals: bool = True,
) -> dict:
    cands = []
    for i, nid in enumerate(ranked):
        score = 1.0 - i * 0.1
        entry = {
            "node_id": nid,
            "node_kind": "service",
            "score": score,
            "confidence": confidence if i == 0 else confidence * 0.5,
            "role": "origin" if i == 0 else "amplifier",
            "signals": {"T": score, "D": 0.5, "P": 0.4} if with_signals else {},
            "dev_vector": {"error_rate": 0.9},
            "explanation": "test",
        }
        cands.append(entry)
    return {
        "incident_id": incident_id,
        "root_cause_service": ranked[0] if ranked else None,
        "failure_type": failure_type,
        "confidence_score": confidence,
        "origin_candidates": cands,
        "true_root_node": true_root,
        "fault_class": fault_class,
    }


def test_ranked_ids_from_candidates():
    assert ranked_ids_from_candidates(
        [{"node_id": "a"}, {"node_id": "b"}]
    ) == ["a", "b"]
    assert ranked_ids_from_candidates(None) == []


def test_evaluate_rows_metrics_path():
    rows = [
        _row(
            incident_id="i1",
            true_root="svc-a",
            ranked=["svc-a", "svc-b", "svc-c"],
            failure_type="Dependency",
            fault_class="Dependency",
        ),
        _row(
            incident_id="i2",
            true_root="svc-x",
            ranked=["svc-y", "svc-x", "svc-z"],
            failure_type=None,
            fault_class="AppLogic",
            confidence=0.4,
        ),
        _row(
            incident_id="i3",
            true_root="node-1",
            ranked=["node-1"],
            failure_type="Infra",
            fault_class="Infra",
            confidence=0.9,
        ),
    ]
    out = evaluate_rows(rows, run_classifier=True)
    assert out["n"] == 3
    assert out["scored"] == 3
    m = out["metrics"]
    assert m["hits_at_1"] == 2
    assert abs(m["top1"] - 2 / 3) < 1e-9
    assert m["hits_at_3"] == 3
    assert m["top3"] == 1.0
    assert abs(m["mrr"] - (1.0 + 0.5 + 1.0) / 3) < 1e-9

    ft = out["failure_type"]
    assert ft["null_count"] == 1
    assert abs(ft["null_rate"] - 1 / 3) < 1e-9
    assert ft["match_rate"] == 1.0
    assert "classifier" in out


def test_failure_type_eval_structural():
    rows = [
        {
            "failure_type": "Infra",
            "fault_class": "Infra",
        },
        {
            "failure_type": None,
            "fault_class": "Resource",
        },
        {
            "failure_type": "Dependency",
            "fault_class": "Resource",
        },
    ]
    r = evaluate_failure_types(rows)
    assert r["null_rate"] == 1 / 3
    assert r["structural_n"] == 3
    assert abs(r["structural_accuracy"] - 1 / 3) < 1e-9


def test_fit_from_rows_grid():
    rows = [
        _row(
            incident_id=f"i{i}",
            true_root="a",
            ranked=["a", "b"],
            failure_type="Infra",
            fault_class="Infra",
        )
        for i in range(3)
    ]
    result = fit_from_rows(rows)
    assert result["n"] == 3
    assert result["grid_size"] > 0
    assert "w_T" in result["best_weights"]
    assert result["best_top1"] == 1.0
    assert result["ece"] is not None
    assert "ece" in result["ece"]
