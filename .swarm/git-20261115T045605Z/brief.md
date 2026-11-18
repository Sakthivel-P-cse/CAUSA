# Commit/push delivery

User explicitly authorized committing and pushing completed implementation on current feature/dev-abc-integration branch, origin https://github.com/Dhev-1/causa.git. Verify current facts. Budget2.86 separate from implementation; exact agent path registered. No extra approvals needed for authorized scope.

Read paths.txt alongside this brief; it is the explicit staging allowlist. Review status, diff/check, new file contents and staged diff for accidental secrets/content. Preserve every unrelated change, including pre-existing docs/onboarding-a-new-app.md, rca/, shared/, scripts/capture_live_baselines.py, scripts/otlp_nodeport_bridge.py and scripts/tt-lite/. Keep .swarm/, agent-info/, agents-communicate.json and runtime/generated artifacts local. Never git add -A or broad add.

Implementation report: agent-info/implementation-2026-11-15/completion-report.md. Evidence:384 Python tests passed,2 existing integration skips;10frontend tests plus lint/build passed; disposable PostgreSQL/Prometheus integration, Helm renders, diff checks passed. No new tests needed unless review reveals a concrete concern.

Stage exact allowlist, git diff --cached --check and review names/content, detailed commit message covering remediation atomic mutation/context/scale/SLO guards, durable operator state and checked legacy import, honest feedback, dashboard contracts/UI clock, exact historical RCA windows, validation and limits. Prefer body-file for multiline text. Commit and push current branch normally to its existing upstream/origin branch. Do not force-push, rebase, merge, reset/discard files, or change remote. Escalate .git writes/network if sandbox requires it using explicit user authorization. If push rejected, report actual cause and preserve committed state.

Save report in this run directory/delivery.md and return commit SHA, branch/remote, push proof, remaining changes. No app edits/deployment/PR creation/delegation. At budget limit stop; do not reset/borrow. Root closes accounting afterward.
