"""Failure classification — Dev C bridge into remediation (spec §6.4)."""

from classify.classifier import FaultClassifier, classify_candidate
from classify.confusion import ConfusionMatrix, score_classification
from classify.failure_type_eval import evaluate_failure_types

__all__ = [
    "ConfusionMatrix",
    "FaultClassifier",
    "classify_candidate",
    "evaluate_failure_types",
    "score_classification",
]
