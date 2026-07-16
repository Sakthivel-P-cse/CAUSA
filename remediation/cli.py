#!/usr/bin/env python3
"""Close the loop: classify → route → approve → canary (demo CLI)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from classify.classifier import FaultClassifier
from fixtures import load_logs, load_ranked_result
from remediation.approval_cli import ApprovalCLI, ApprovalDecision
from remediation.canary import SimulatedCanary
from remediation.router import RemediationRouter


def main() -> int:
    parser = argparse.ArgumentParser(description="Causa remediation approval CLI")
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Non-interactive: approve first candidate (for demos/CI)",
    )
    parser.add_argument(
        "--breach",
        action="store_true",
        help="Simulate SLO breach during canary (demo rollback)",
    )
    args = parser.parse_args()

    ranked = load_ranked_result()
    logs = load_logs()
    logs_by_node = {k: [e["message"] for e in v] for k, v in logs.items()}
    clf = FaultClassifier(logs_by_node)
    router = RemediationRouter()

    plans = []
    for cand in ranked.primary_candidates:
        pred = clf.classify(cand)
        plans.append(router.route(pred.fault_class, cand, ranked))

    if args.auto_approve:
        outcome_decision = ApprovalDecision.APPROVE
        idx = 0
        plan = plans[0]
        print(json.dumps({"decision": "approve", "plan": plan.to_dict()}, indent=2))
    else:
        cli = ApprovalCLI()
        outcome = cli.present(ranked, plans)
        outcome_decision = outcome.decision
        idx = outcome.candidate_index
        plan = outcome.plan
        if outcome_decision != ApprovalDecision.APPROVE or plan is None:
            print("No remediation applied.")
            return 1

    metric_fn = SimulatedCanary.breaching_metrics if args.breach else None
    canary = SimulatedCanary(metric_fn=metric_fn).apply()
    print(
        json.dumps(
            {
                "approved_index": idx,
                "plan": plan.to_dict() if plan else None,
                "canary": canary.to_dict(),
            },
            indent=2,
        )
    )
    return 0 if (canary.applied or canary.rolled_back) else 1


if __name__ == "__main__":
    raise SystemExit(main())
