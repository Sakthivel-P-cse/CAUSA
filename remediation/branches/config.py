"""Config deep branch — before/after config patch proposal."""

from __future__ import annotations

from typing import Any

from contracts.c3_candidates import RootCauseCandidate


def propose_config_patch(candidate: RootCauseCandidate) -> dict[str, Any]:
    before = {
        "service": candidate.node_id,
        "flag": "feature.risky",
        "value": True,
        "replicas": 2,
    }
    after = {
        "service": candidate.node_id,
        "flag": "feature.risky",
        "value": False,
        "replicas": 2,
    }
    return {
        "patch": {
            "target": f"configmaps/{candidate.node_id}",
            "before": before,
            "after": after,
            "diff": (
                f"- feature.risky: true\n"
                f"+ feature.risky: false\n"
            ),
        },
        "evidence_trail": candidate.to_dict(),
    }
