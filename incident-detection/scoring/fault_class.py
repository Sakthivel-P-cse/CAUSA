"""Structural fault-class floor for the top candidate.

`RankedResult.fault_class` was never assigned anywhere in the RCA path, so it
defaulted to None and `store.insert_report` wrote NULL into `rca_reports
.failure_type` on every report — a column with a dedicated index and a
documented enum constraint.

Full classification is Dev C's target (see `contracts.ground_truth.FaultClass`),
and this module deliberately does NOT try to be that classifier. It assigns only
the classes the kernel can already justify from structure it computed itself,
and returns None everywhere else rather than guessing. A NULL that means "not
classified" is honest; a wrong label that looks authoritative is not.

Assigned here:
  Infra      — the top candidate is a HOST (something runs on it).
  Dependency — the top candidate is an uninstrumented backing store / queue.
  Resource   — the top candidate's dominant deviating metric is a resource one
               (cpu / saturation) rather than a RED symptom.

Everything else (AppLogic vs Config vs Data) needs evidence the kernel does not
currently gather — log content, config/deploy history, schema and query shape —
so it stays None until Dev C's classifier lands.

The layer rules come first because node kind is structural evidence about WHAT
failed, while the dominant metric only says HOW it presented; a saturated host
is an Infra fault, not a Resource one, which is what the S1/S5 ground truth says.
"""

from __future__ import annotations

from contracts import CallType, NodeKind, RootCauseCandidate
from contracts.ground_truth import FaultClass
from graphs.influence_graph import InfluenceGraph

# dev_vector keys that indicate resource exhaustion rather than a RED symptom.
_RESOURCE_METRICS = frozenset({"cpu", "saturation"})


def _dominant_metric(candidate: RootCauseCandidate) -> str | None:
    """The metric contributing the candidate's max-composite anomaly score.

    `dev_vector` mirrors the C2 per-metric deviations, and anomaly_score is the
    max over them, so the argmax is the metric that actually fired. Ties break
    lexically to keep the assignment deterministic.
    """
    dv = {k: v for k, v in (candidate.dev_vector or {}).items() if k != "anomaly"}
    if not dv:
        return None
    peak = max(dv.values())
    if peak <= 0.0:
        return None
    return min(k for k, v in dv.items() if v == peak)


def _is_host(node_id: str, ig: InfluenceGraph) -> bool:
    """True when `node_id` hosts something — i.e. it sources a runs-on edge.

    `NodeKind.INFRA` now means exactly this (see the NodeKind docstring), so the
    kind would usually do. Checking the runs-on relationship directly is kept as
    defense in depth: mistyping a store as INFRA is the specific error this
    module already shipped once, it is invisible until a report is mislabelled,
    and re-deriving it from the edges costs one scan of a small graph.
    """
    return any(
        e.src == node_id and e.call_type == CallType.RUNS_ON for e in ig.edges
    )


def derive_fault_class(
    candidates: list[RootCauseCandidate], ig: InfluenceGraph
) -> str | None:
    """Structural fault class for the ranked list, or None when unclassifiable.

    Reads the top candidate only — the class describes the incident's root, and
    the list is already sorted with arbitration applied.
    """
    if not candidates:
        return None
    top = candidates[0]

    # A hosting node at the top means the fault is in the layer beneath the
    # workloads. Arbitration having promoted it there is the same signal.
    if _is_host(top.node_id, ig):
        return FaultClass.INFRA.value

    # An uninstrumented sink at the top is a failing backing store or queue.
    # Only assignable since INFRA/DEPENDENCY were separated — while a DB could
    # be typed either way, this rule could not be trusted.
    if top.node_kind == NodeKind.DEPENDENCY:
        return FaultClass.DEPENDENCY.value

    if _dominant_metric(top) in _RESOURCE_METRICS:
        return FaultClass.RESOURCE.value

    # Not enough evidence in the kernel to separate the remaining classes.
    return None
