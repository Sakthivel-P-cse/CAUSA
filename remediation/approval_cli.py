"""Human-gated approval CLI (spec §6.2). Web UI deferred."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, TextIO

from contracts.c3_candidates import RankedResult
from remediation.router import RemediationPlan


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    QUIT = "quit"


@dataclass
class ApprovalOutcome:
    decision: ApprovalDecision
    candidate_index: int
    plan: RemediationPlan | None


class ApprovalCLI:
    """Show ranked candidates + evidence + proposed remediation; approve or reject."""

    def __init__(
        self,
        input_fn: Callable[[str], str] | None = None,
        stdout: TextIO | None = None,
    ):
        import sys

        self.input_fn = input_fn or input
        self.stdout = stdout or sys.stdout

    def present(
        self,
        ranked: RankedResult,
        plans: list[RemediationPlan],
    ) -> ApprovalOutcome:
        cands = ranked.primary_candidates
        for idx, (cand, plan) in enumerate(zip(cands, plans)):
            self._print_candidate(idx, cand, plan)
            raw = self.input_fn(
                f"[{idx}] approve / reject / quit > "
            ).strip().lower()
            if raw in ("a", "approve", "y", "yes"):
                return ApprovalOutcome(ApprovalDecision.APPROVE, idx, plan)
            if raw in ("q", "quit", "exit"):
                return ApprovalOutcome(ApprovalDecision.QUIT, idx, plan)
            # reject → next
            self.stdout.write("Rejected; trying next candidate.\n")
        return ApprovalOutcome(ApprovalDecision.REJECT, len(cands) - 1, None)

    def _print_candidate(self, idx: int, cand, plan: RemediationPlan) -> None:
        self.stdout.write("\n" + "=" * 60 + "\n")
        self.stdout.write(f"Candidate #{idx}: {cand.node_id}  score={cand.score:.3f}  conf={cand.confidence:.3f}\n")
        self.stdout.write(f"Role: {cand.role.value}  Kind: {cand.node_kind.value}\n")
        self.stdout.write(f"Explanation: {cand.explanation}\n")
        self.stdout.write(f"\nProposed remediation ({plan.depth}): {plan.action}\n")
        self.stdout.write(f"  {plan.summary}\n")
        if plan.action == "code_fix_pr":
            pr = plan.details.get("pr", {})
            self.stdout.write(f"  PR title: {pr.get('title')}\n")
            self.stdout.write(f"  merged={pr.get('merged')} draft={pr.get('draft')}\n")
        elif plan.action == "config_patch":
            patch = plan.details.get("patch", {})
            self.stdout.write(f"  Diff:\n{patch.get('diff')}\n")
        elif plan.action == "rollback_to_last_good":
            rb = plan.details.get("rollback", {})
            self.stdout.write(f"  Target: {rb.get('target')} → {rb.get('last_good_revision')}\n")
