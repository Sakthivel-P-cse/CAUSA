"""Classify top candidate into AppLogic|Config|Infra|Dependency|Resource|Data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from contracts.c3_candidates import FaultClass, NodeKind, RootCauseCandidate


# Log / message signatures (natural confounding — rules, not ML)
_PATTERNS: list[tuple[FaultClass, re.Pattern[str]]] = [
    (FaultClass.DEPENDENCY, re.compile(r"redis|kafka|timeout|connection refused|upstream|circuit", re.I)),
    (FaultClass.RESOURCE, re.compile(r"oom|maxmemory|cpu|memory|saturation|throttl", re.I)),
    (FaultClass.CONFIG, re.compile(r"flagd|feature.?flag|config|misconfig|env var|manifest", re.I)),
    (FaultClass.INFRA, re.compile(r"node.?notready|cordon|kubelet|disk pressure|host", re.I)),
    (FaultClass.DATA, re.compile(r"corrupt|schema|migration|constraint|null.?pointer.?data", re.I)),
    (FaultClass.APP_LOGIC, re.compile(r"panic|exception|nil pointer|assertion|bug|stack trace", re.I)),
]


def _score_from_logs(messages: Sequence[str]) -> dict[FaultClass, float]:
    scores = {fc: 0.0 for fc in FaultClass}
    for msg in messages:
        for fc, pat in _PATTERNS:
            if pat.search(msg):
                scores[fc] += 1.0
    return scores


def _score_from_dev_vector(dev: dict[str, float]) -> dict[FaultClass, float]:
    scores = {fc: 0.0 for fc in FaultClass}
    sat = max(dev.get("saturation", 0.0), dev.get("cpu", 0.0), dev.get("memory", 0.0))
    err = dev.get("error_rate", 0.0)
    retry = dev.get("retry_rate", 0.0)
    if sat >= 0.8:
        scores[FaultClass.RESOURCE] += sat
    if retry >= 0.5 or err >= 0.4:
        scores[FaultClass.DEPENDENCY] += max(retry, err)
    if sat < 0.3 and err >= 0.5:
        scores[FaultClass.APP_LOGIC] += err
    return scores


def _score_from_kind(kind: NodeKind) -> dict[FaultClass, float]:
    scores = {fc: 0.0 for fc in FaultClass}
    if kind == NodeKind.DEPENDENCY:
        scores[FaultClass.DEPENDENCY] += 1.5
    elif kind == NodeKind.INFRA:
        scores[FaultClass.INFRA] += 1.5
    elif kind == NodeKind.SERVICE:
        scores[FaultClass.APP_LOGIC] += 0.3
    return scores


@dataclass
class ClassificationResult:
    fault_class: FaultClass
    scores: dict[str, float]
    rationale: str


def classify_candidate(
    candidate: RootCauseCandidate,
    log_messages: Sequence[str] | None = None,
) -> ClassificationResult:
    logs = list(log_messages or [])
    combined: dict[FaultClass, float] = {fc: 0.0 for fc in FaultClass}
    for part in (
        _score_from_logs(logs),
        _score_from_dev_vector(candidate.dev_vector),
        _score_from_kind(candidate.node_kind),
    ):
        for fc, v in part.items():
            combined[fc] += v

    # Prefer highest score; stable tie-break by enum order
    best = max(FaultClass, key=lambda fc: (combined[fc], -list(FaultClass).index(fc)))
    if all(v == 0.0 for v in combined.values()):
        # Fallback from node kind
        if candidate.node_kind == NodeKind.DEPENDENCY:
            best = FaultClass.DEPENDENCY
        elif candidate.node_kind == NodeKind.INFRA:
            best = FaultClass.INFRA
        else:
            best = FaultClass.APP_LOGIC

    rationale = (
        f"class={best.value} from node_kind={candidate.node_kind.value}, "
        f"role={candidate.role.value}, top_dev={max(candidate.dev_vector.values()) if candidate.dev_vector else 0:.2f}, "
        f"logs={len(logs)}"
    )
    return ClassificationResult(
        fault_class=best,
        scores={k.value: v for k, v in combined.items()},
        rationale=rationale,
    )


class FaultClassifier:
    """Callable classifier; optional log adapter lookup by node."""

    def __init__(self, logs_by_node: dict[str, list[str]] | None = None):
        self.logs_by_node = logs_by_node or {}

    def classify(self, candidate: RootCauseCandidate) -> ClassificationResult:
        msgs = self.logs_by_node.get(candidate.node_id, [])
        return classify_candidate(candidate, msgs)
