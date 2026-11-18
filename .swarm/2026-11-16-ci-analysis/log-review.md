# CI run 43739843287

- Workflow: **Causa ABC Integration CI**; branch `feature/dev-abc-integration`; SHA `2d49b487dd3689da28c0c035b9681c7802027613`; overall conclusion: **failure**.
- Failed job: `unit-and-golden` (job 132890349018). Its only failed step was #14, **Fixture-smoke artifacts** (`PYTHONPATH=. python -m eval.report`).
- Exact terminal error: `incident-detection/eval/report.py`, line 160, indexes `canary_rb['rolled_back']`; raised `KeyError: 'rolled_back'`; process exited 1.
- That step also logged `dependency propose failed` for `product-catalog`; Kubernetes discovery requests to `http://localhost:8080/api?timeout=32s` failed with `dial tcp [::1]:8080: connect: connection refused`, followed by `The connection to the server localhost:8080 was refused - did you specify the right host or port?`
- Steps #1–13 all passed: Set up job; Initialize containers; checkout; setup Python; setup Helm; install package + deps; load schema into CI Postgres; Python dependency audit; full Python test suite; assert Postgres blocker; Dev C lane + golden smoke; demo loop; NIM client + remediation service. Notably, the demo loop and NIM step are marked successful despite their logs containing the same localhost:8080 refusal for `checkout`.
- Steps #15–18 were skipped: Check Phase-2 rule packs, Helm lint, Live eval unit tests (mocked PG), Upload artifacts. Cleanup and completion steps succeeded.
- Separate `frontend` job succeeded, including `npm ci`, audit, lint, test, and build.

Evidence: `gh run view 43739843287 --log-failed`; `gh run view 43739843287 --json databaseId,headBranch,headSha,name,status,conclusion,jobs,url`.
