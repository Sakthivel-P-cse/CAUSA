"""Fitted weights + optional confidence calibration artifact gates."""

from __future__ import annotations

import json

from contracts import NodeKind, Role, RootCauseCandidate, SignalScores
from scoring.fitted_weights import (
    apply_calibration_map,
    load_calibration_map,
    load_fitted_weights,
)
from scoring.output import stamp_confidence


def _write_fit(path, *, weights=None, calibration=None):
    body: dict = {}
    if weights is not None:
        body["best_weights"] = weights
    if calibration is not None:
        body["calibration"] = calibration
    path.write_text(json.dumps(body))
    return path


def test_missing_artifact_equal_thirds(tmp_path):
    w = load_fitted_weights(tmp_path / "missing.json")
    assert abs(w.w_T - 1 / 3) < 1e-9
    assert abs(w.w_D - 1 / 3) < 1e-9
    assert abs(w.w_P - 1 / 3) < 1e-9


def test_degenerate_weights_rejected(tmp_path):
    p = _write_fit(tmp_path / "fit.json", weights={"w_T": 0.0, "w_D": 0.0, "w_P": 1.0})
    w = load_fitted_weights(p)
    assert abs(w.w_T - 1 / 3) < 1e-9
    assert abs(w.w_P - 1 / 3) < 1e-9


def test_valid_weights_loaded_and_normalized(tmp_path):
    p = _write_fit(tmp_path / "fit.json", weights={"w_T": 0.2, "w_D": 0.3, "w_P": 0.5})
    w = load_fitted_weights(p)
    assert abs(w.w_T - 0.2) < 1e-9
    assert abs(w.w_D - 0.3) < 1e-9
    assert abs(w.w_P - 0.5) < 1e-9


def test_env_artifact_override(tmp_path, monkeypatch):
    p = _write_fit(tmp_path / "env_fit.json", weights={"w_T": 0.4, "w_D": 0.4, "w_P": 0.2})
    monkeypatch.setenv("RCA_WEIGHTS_ARTIFACT", str(p))
    w = load_fitted_weights()
    assert abs(w.w_T - 0.4) < 1e-9
    assert abs(w.w_P - 0.2) < 1e-9


def test_calibration_rejected_when_too_few_or_constant(tmp_path):
    few = [{"raw": i / 20, "calibrated": 0.5 + i / 40} for i in range(5)]
    p = _write_fit(tmp_path / "few.json", calibration={"mapping": few})
    assert load_calibration_map(p) is None

    constant = [{"raw": i / 20, "calibrated": 1.0} for i in range(12)]
    p2 = _write_fit(tmp_path / "const.json", calibration={"mapping": constant})
    assert load_calibration_map(p2) is None


def test_calibration_loaded_and_applied(tmp_path):
    mapping = [{"raw": i / 20.0, "calibrated": 0.1 + 0.05 * i} for i in range(12)]
    p = _write_fit(tmp_path / "cal.json", calibration={"mapping": mapping})
    loaded = load_calibration_map(p)
    assert loaded is not None
    assert len(loaded) == 12
    assert apply_calibration_map(0.0, loaded) == loaded[0][1]


def test_stamp_confidence_uses_calibration_when_safe(tmp_path, monkeypatch):
    mapping = [{"raw": i / 20.0, "calibrated": min(1.0, 0.05 + 0.08 * i)} for i in range(12)]
    p = _write_fit(
        tmp_path / "cal.json",
        weights={"w_T": 0.3, "w_D": 0.3, "w_P": 0.4},
        calibration={"mapping": mapping},
    )
    monkeypatch.setenv("RCA_WEIGHTS_ARTIFACT", str(p))

    cands = [
        RootCauseCandidate(
            node_id="a",
            node_kind=NodeKind.SERVICE,
            score=0.9,
            confidence=0.0,
            role=Role.ORIGIN,
            signals=SignalScores(T=0.9, D=0.5, P=0.5),
            explanation="a (T=0.90), out-influence=0.00, in-influence=0.00 (D=0.50), 0/0 outgoing edges consistent in order (P=0.50). Role: ORIGIN.",
        ),
        RootCauseCandidate(
            node_id="b",
            node_kind=NodeKind.SERVICE,
            score=0.4,
            confidence=0.0,
            role=Role.AMPLIFIER,
            signals=SignalScores(T=0.4, D=0.4, P=0.4),
            explanation="b (T=0.40), out-influence=0.00, in-influence=0.00 (D=0.40), 0/0 outgoing edges consistent in order (P=0.40). Role: AMPLIFIER.",
        ),
    ]
    stamp_confidence(cands)
    assert cands[0].confidence >= cands[1].confidence
    assert cands[0].confidence > 0.0
