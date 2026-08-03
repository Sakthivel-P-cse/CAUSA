#!/usr/bin/env python3
"""Classify → route → print typed plan (execution is the remediation worker)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters.logs import messages_from_fixture_logs
from classify.classifier import FaultClassifier
from fixtures import load_logs, load_ranked_result
from remediation.approval_cli import ApprovalCLI, ApprovalDecision
from remediation.router import RemediationRouter


def main() -> int:
    parser = argparse.ArgumentParser(description="Causa remediation approval CLI")
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Non-interactive: approve first candidate (prints typed plan only)",
    )
    parser.add_argument(
        "--breach",
        action="store_true",
        help="Deprecated no-op (simulated breach removed)",
    )
    args = parser.parse_args()
    if args.breach:
        print("warning: --breach is deprecated; live SLO gates run in the worker", file=sys.stderr)

    ranked = load_ranked_result()
    logs_by_node = messages_from_fixture_logs(load_logs())
    clf = FaultClassifier(logs_by_node)
    router = RemediationRouter()

    plans = []
    for cand in ranked.candidates:
        pred = clf.classify(cand)
        plans.append(router.route(pred.fault_class, cand, ranked, incident_id=ranked.incident_id or ""))

    if args.auto_approve:
        outcome_decision = ApprovalDecision.APPROVE
        idx = 0
        plan = plans[0]
    else:
        cli = ApprovalCLI()
        outcome = cli.present(ranked, plans)
        outcome_decision = outcome.decision
        idx = outcome.candidate_index
        plan = outcome.plan
        if outcome_decision != ApprovalDecision.APPROVE or plan is None:
            print("No remediation applied.")
            return 1

    print(
        json.dumps(
            {
                "approved_index": idx,
                "plan": plan.to_dict() if plan else None,
                "execution": "queued_for_worker",
                "note": "Approve via API; remediation.worker claims and executes real backends",
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
