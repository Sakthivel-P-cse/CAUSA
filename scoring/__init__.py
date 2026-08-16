"""Deterministic T/D/P scoring, arbitration, and Scorer interface."""

from .arbitration import arbitrate
from .output import build_candidates, validate_explanation
from .scorer import DEFAULT_WEIGHTS, Scorer, score
from .signals import SignalResult, compute_signals

__all__ = [
    "DEFAULT_WEIGHTS",
    "Scorer",
    "SignalResult",
    "arbitrate",
    "build_candidates",
    "compute_signals",
    "score",
    "validate_explanation",
]
