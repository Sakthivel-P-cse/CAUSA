"""Integration tests for the Dev C ← Dev A+B seam.

`tests/test_dev_c.py` exercises Dev C's modules in isolation against hand-built
golden fixtures. These tests instead drive the **real** A+B RCA kernel end to end
(`build_incident_subgraph → build_influence_graph → score`) and prove that its
`RankedResult` — the actual C3 contract output — flows correctly into every Dev C
consumer (classify, remediation, eval) *only through `contracts/` and `adapters/`*.

That boundary is the whole point of the integration: if a B-side change altered
the shape B emits, or a C-side change reached past the contract into a B/A
internal, these tests fail where the lane tests would not.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- Dev A+B (producers) — imported here only to construct real kernel output ---
from fixtures.incident_s0 import load_incident_s0
from fixtures.incident_s1 import load_incident_s1
from graphs.incident_subgraph import build_incident_subgraph
from graphs.influence_graph import build_influence_graph
from scoring.scorer import score

# --- The seam ---
from adapters.scorer import ABScorer
from adapters.isolation import IsolationView

# --- Dev C (consumers) — must reach A/B only via contracts + adapters ---
from classify.classifier import classify_candidate
from remediation.router import RemediationRouter
from eval.metrics import compute_metrics, subgraph_recall
from eval.runner import EvalRunner
from contracts import (
    ABSTENTION_FLAG,
    FaultClass,
    NodeKind,
    RankedResult,
    Role,
    RootCauseCandidate,
    SignalScores,
)


# --------------------------------------------------------------------------- #
# Helpers: run the real A+B kernel to a C3 RankedResult                        #
# --------------------------------------------------------------------------- #
def _kernel_rank(loader):
    """Drive the real Dev B kernel on a synthetic fixture → C3 RankedResult."""
    topology, traces, anomaly, gt = loader()
    sub = build_incident_subgraph(topology, traces, anomaly)
    ig = build_influence_graph(sub)
    result = score(sub, ig=ig, incident_id=gt.incident_id)
    return result, gt


# --------------------------------------------------------------------------- #
# 1. Real B kernel output is a well-formed C3 contract                         #
# --------------------------------------------------------------------------- #
def test_kernel_output_is_valid_c3_contract():
    result, gt = _kernel_rank(load_incident_s0)
    assert isinstance(result, RankedResult)
    assert result.incident_id == gt.incident_id
    assert not result.abstained
    assert result.candidates, "kernel produced no candidates for a clear-origin incident"
    for c in result.candidates:
        assert isinstance(c, RootCauseCandidate)
        assert isinstance(c.node_kind, NodeKind)
        assert isinstance(c.role, Role)
        assert isinstance(c.signals, SignalScores)
        assert 0.0 <= c.confidence  # confidence is a non-negative raw score
    # ranked_node_ids() is the exact view every C consumer reads
    assert result.candidates[0].node_id in result.ranked_node_ids()


# --------------------------------------------------------------------------- #
# 2. Real B RankedResult → Dev C classify (dependency-origin incident)         #
# --------------------------------------------------------------------------- #
def test_kernel_result_flows_into_classify_s0_dependency():
    result, _ = _kernel_rank(load_incident_s0)
    top = result.candidates[0]
    assert top.node_id == "cart-db"  # DB origin survives sync-edge reversal
    pred = classify_candidate(top)
    # cart-db is NodeKind.DEPENDENCY → classifier lands on Dependency even
    # without logs, purely from the C3 candidate B handed across the seam.
    assert pred.fault_class == FaultClass.DEPENDENCY


def test_kernel_result_flows_into_classify_s1_infra():
    result, _ = _kernel_rank(load_incident_s1)
    infra = next(c for c in result.candidates if c.node_id == "node-b")
    assert infra.node_kind == NodeKind.INFRA
    pred = classify_candidate(infra)
    assert pred.fault_class == FaultClass.INFRA


# --------------------------------------------------------------------------- #
# 3. Real B RankedResult → Dev C remediation router                           #
# --------------------------------------------------------------------------- #
def test_kernel_result_flows_into_remediation():
    result, _ = _kernel_rank(load_incident_s0)
    top = result.candidates[0]
    plan = RemediationRouter().route(FaultClass.DEPENDENCY, top, result)
    assert plan.depth == "deep"
    assert plan.action == "rollback_to_last_good"
    # Router must accept the candidate/result B produced without massaging.
    assert plan.candidate is top
    assert plan.to_dict()["candidate_node"] == top.node_id


# --------------------------------------------------------------------------- #
# 4. Real B RankedResult → Dev C eval metrics vs fixture ground truth          #
# --------------------------------------------------------------------------- #
def test_kernel_result_scores_top1_against_ground_truth():
    result, gt = _kernel_rank(load_incident_s0)
    ranked = result.ranked_node_ids()
    assert subgraph_recall(gt.true_root_node, ranked) is True
    metrics = compute_metrics(
        [(gt.true_root_node, ranked, ranked, "S0")]
    )
    assert metrics.n == 1
    assert metrics.per_scenario["S0"].top1 == 1.0


# --------------------------------------------------------------------------- #
# 5. The B→C serialization seam (rca_reports → causa_eval read-back)           #
#    A report B persists is JSON; C reads it back and must reconstruct an      #
#    equivalent RankedResult that its consumers still accept.                  #
# --------------------------------------------------------------------------- #
def test_ranked_result_survives_dict_roundtrip_across_seam():
    result, _ = _kernel_rank(load_incident_s0)
    roundtrip = RankedResult.from_dict(result.to_dict())

    assert roundtrip.incident_id == result.incident_id
    assert roundtrip.abstained == result.abstained
    assert roundtrip.ranked_node_ids() == result.ranked_node_ids()

    # The rehydrated result is still fully consumable downstream.
    top = roundtrip.candidates[0]
    assert classify_candidate(top).fault_class == FaultClass.DEPENDENCY
    plan = RemediationRouter().route(FaultClass.DEPENDENCY, top, roundtrip)
    assert plan.depth == "deep"


# --------------------------------------------------------------------------- #
# 6. ABScorer C4 seam: Dev C Weights/EnabledSignals → real B scorer → C        #
# --------------------------------------------------------------------------- #
def test_abscorer_seam_end_to_end_on_golden():
    runner = EvalRunner.from_golden()
    report = runner.evaluate_scorer(ABScorer(), method="ABFull")
    assert report.metrics.subgraph_recall == 1.0
    assert report.metrics.top1 == 1.0


def test_abscorer_output_feeds_classify_and_remediation():
    # ABScorer delegates to the real kernel Scorer and returns a C3 RankedResult;
    # that result must be directly consumable by C's classify + remediation.
    result = ABScorer().score({"incident_id": "golden-0001"})
    assert isinstance(result, RankedResult)
    assert result.candidates
    top = result.candidates[0]
    pred = classify_candidate(top)
    assert pred.fault_class in set(FaultClass)
    plan = RemediationRouter().route(pred.fault_class, top, result)
    assert plan.candidate is top
    assert plan.depth in {"deep", "stub"}


# --------------------------------------------------------------------------- #
# 7. Abstention is a first-class outcome across the seam                       #
#    (docs/dev_c_handoff.md: an abstention is NOT a wrong answer).             #
# --------------------------------------------------------------------------- #
def test_abstention_propagates_through_c_consumers():
    abstained = RankedResult(
        candidates=[],
        incident_id="ambiguous-1",
        abstain_reason=ABSTENTION_FLAG,
    )
    assert abstained.abstained
    assert abstained.ranked_node_ids() == []

    # Survives the persistence round-trip as an abstention, not an empty result.
    roundtrip = RankedResult.from_dict(abstained.to_dict())
    assert roundtrip.abstained

    # IsolationView treats it as an abstention (no phantom clusters).
    view = IsolationView.abstain(abstained.incident_id, abstained.abstain_reason)
    assert view.abstained
    assert view.cluster_count == 0


# --------------------------------------------------------------------------- #
# 8. Architectural invariant: Dev C lanes reach A/B ONLY via contracts+adapters #
#    This is the load-bearing integration guarantee (Team_Split §3). A C-side  #
#    file that imports graphs/scoring/isolation/ingest/rca/anomaly directly    #
#    breaks the seam even if every other test still passes.                    #
# --------------------------------------------------------------------------- #
FORBIDDEN_TOP_LEVEL = {
    "graphs", "scoring", "isolation", "ingest", "rca", "anomaly", "testbed",
}
DEV_C_PACKAGES = ("classify", "remediation", "eval")


def _forbidden_imports(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text(), filename=str(py_file))
    bad: list[str] = []
    for node in ast.walk(tree):
        roots: list[str] = []
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots = [node.module.split(".")[0]]
        for r in roots:
            if r in FORBIDDEN_TOP_LEVEL:
                bad.append(f"{py_file.relative_to(ROOT)}: {r}")
    return bad


@pytest.mark.parametrize("pkg", DEV_C_PACKAGES)
def test_dev_c_lane_has_no_direct_ab_imports(pkg):
    violations: list[str] = []
    for py_file in (ROOT / pkg).rglob("*.py"):
        violations.extend(_forbidden_imports(py_file))
    assert not violations, (
        "Dev C must reach A/B only through contracts/ + adapters/. "
        f"Direct imports found: {violations}"
    )
