"""AppLogic deep branch — proposed code-fix PR (never auto-merged)."""

from __future__ import annotations

from typing import Any

from contracts import RankedResult, RootCauseCandidate


def propose_code_fix_pr(
    candidate: RootCauseCandidate,
    ranked: RankedResult | None = None,
) -> dict[str, Any]:
    title = f"fix({candidate.node_id}): proposed remediation from Causa RCA"
    body = "\n".join(
        [
            "## Evidence",
            candidate.explanation or "(no explanation)",
            "",
            f"- score: {candidate.score:.3f}",
            f"- confidence: {candidate.confidence:.3f}",
            f"- role: {candidate.role.value}",
            f"- signals: T={candidate.signals.T:.2f} D={candidate.signals.D:.2f} P={candidate.signals.P:.2f}",
            "",
            "## Proposed diff (payload only — never auto-merged)",
            "```diff",
            f"--- a/services/{candidate.node_id}/handler.py",
            f"+++ b/services/{candidate.node_id}/handler.py",
            "@@",
            "- # buggy path",
            "+ # guarded path with timeout + retry budget",
            "```",
            "",
            "**Status:** proposed PR payload only — never auto-merged.",
        ]
    )
    return {
        "pr": {
            "title": title,
            "body": body,
            "base": "main",
            "head": f"causa/fix-{candidate.node_id}",
            "merged": False,
            "draft": True,
        },
        "evidence_trail": candidate.to_dict(),
    }
