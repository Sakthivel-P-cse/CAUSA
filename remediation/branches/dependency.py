"""Dependency deep branch — rollback-to-last-good / circuit-break."""

from __future__ import annotations

from typing import Any

from contracts.c3_candidates import RootCauseCandidate


def propose_rollback(candidate: RootCauseCandidate) -> dict[str, Any]:
    return {
        "rollback": {
            "target": candidate.node_id,
            "strategy": "rollback_to_last_good",
            "last_good_revision": "rev-previous",
            "circuit_break": True,
            "steps": [
                f"Open circuit on callers of {candidate.node_id}",
                f"Roll {candidate.node_id} to last-good revision",
                "Watch error_rate / latency SLOs",
                "Close circuit when healthy",
            ],
        },
        "evidence_trail": candidate.to_dict(),
    }
