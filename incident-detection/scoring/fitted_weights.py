"""Fitted scoring defaults from C live eval (eval/fit_from_live artifacts).

Equal thirds remain the boot defaults until a live fit writes
`eval/artifacts/fit_result.json`. Call `load_fitted_weights()` from the scorer
or orchestrator when applying C→B handoff.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from contracts import Weights

logger = logging.getLogger(__name__)

_DEFAULT = Weights(w_T=1.0 / 3.0, w_D=1.0 / 3.0, w_P=1.0 / 3.0)
_ARTIFACT = Path(__file__).resolve().parents[1] / "eval" / "artifacts" / "fit_result.json"


def load_fitted_weights(path: Path | None = None) -> Weights:
    p = path or _ARTIFACT
    if not p.exists():
        return _DEFAULT
    try:
        raw = json.loads(p.read_text())
        bw = raw.get("best_weights") or raw.get("recommended_weights") or {}
        return Weights(
            w_T=float(bw.get("w_T", _DEFAULT.w_T)),
            w_D=float(bw.get("w_D", _DEFAULT.w_D)),
            w_P=float(bw.get("w_P", _DEFAULT.w_P)),
        )
    except Exception as exc:
        logger.warning("failed loading fitted weights from %s: %s", p, exc)
        return _DEFAULT
