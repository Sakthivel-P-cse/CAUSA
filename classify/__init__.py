"""Failure classification — Dev C bridge into remediation (spec §6.4)."""

from classify.classifier import FaultClassifier, classify_candidate
from classify.confusion import ConfusionMatrix, score_classification

__all__ = [
    "ConfusionMatrix",
    "FaultClassifier",
    "classify_candidate",
    "score_classification",
]
