"""Routing-table stubs for Infra / Resource / Data."""

from __future__ import annotations

from typing import Any

from contracts import FaultClass, RootCauseCandidate


def stub_action(fault_class: FaultClass, candidate: RootCauseCandidate) -> dict[str, Any]:
    table = {
        FaultClass.INFRA: f"STUB: scale/restart/cordon for infra target {candidate.node_id}",
        FaultClass.RESOURCE: f"STUB: scale-up proposal for {candidate.node_id}",
        FaultClass.DATA: f"STUB: alert-only for data fault on {candidate.node_id}",
    }
    return {
        "summary": table[fault_class],
        "implemented": False,
        "target": candidate.node_id,
        "evidence_trail": candidate.to_dict(),
    }
