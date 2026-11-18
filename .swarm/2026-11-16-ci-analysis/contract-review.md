# Canary report contract trace

## Scope and evidence

- CI run `43739843287`, branch `feature/dev-abc-integration`, HEAD `2d49b487dd3689da28c0c035b9681c7802027613`. Hosted job details and terminal output are recorded in [log-review.md](log-review.md); direct GitHub access was unavailable in this inspection.
- Source inspection used the committed `incident-detection/eval/report.py` at HEAD and the shared worktree after the lead's one-line edit.

## Execution and shape

1. `.github/workflows/eval.yml:89-90` invokes `PYTHONPATH=. python -m eval.report` in the **Fixture-smoke artifacts** step. Module execution reaches `main()` (`report.py:169-170`), which calls `generate_all()`.
2. `generate_all()` constructs `canary_rb` at `report.py:76-77` with exactly `{"mode": "argo_abort", "note": "rollback via Rollout undo / inverse patch"}` and inserts that same mapping at `report.py:118-123` as `report["canary_rollback"]`.
3. At committed HEAD, `report.py:160` instead formats `canary_rb["rolled_back"]`. No such key is present, so the CI `KeyError` occurs while composing `DEMO.md`. Before reaching that interpolation, `demo_report.json`, `ablation_table.txt`, `confusion_matrix.txt`, `reliability_diagram.txt`, `per_scenario_table.json`, and `cross_topology_argument.txt` are written (`report.py:125-136`). The `DEMO.md` write does not complete and `generate_all()` never reaches its return at line 166; consequently `main()` does not print its summary.
4. Current shared-worktree diff changes the rendered DEMO line to `canary_rb["note"]` (now “Canary rollback: …”), matching the local mapping. It does not change the JSON report shape.

## Callers, related shapes, and tests

- Repository search finds only `main()` calling `generate_all()`, and only the workflow module invocation above as an execution entry point. No test imports/calls `eval.report`, checks `canary_rollback`, or checks `CanaryResult`/`rolled_back`.
- `incident-detection/remediation/canary.py:9-20` is a separate deprecated `CanaryResult` serializer with keys `applied`, `rolled_back`, and `snapshots`; `SimulatedCanary.apply()` raises at lines 46-49. Search finds no use of it in `eval/report.py` or its callers. It is not the producer of this report's `canary_rb` mapping.
- Existing rollback tests cover remediation worker/apply behavior, not report artifact generation (`tests/test_worker_rollback_safety.py`, `tests/test_remediation_apply_safety.py`). `README_DEVC.md:23-25` describes the report as an n=1 fixture smoke gate; it does not specify a boolean rollback field.
- The workflow log records Kubernetes discovery refusal to `localhost:8080` during this same step and earlier successful steps. Source shows that `eval.report` itself does not call `kubectl`; the KeyError is independently explained by the report/template key mismatch. This inspection does not establish whether the Kubernetes refusal affected report content or prior successful steps.

## Check result

- Read-only source/caller/test search: complete for `generate_all`, `eval.report`, `canary_rollback`, `CanaryResult`, and `rolled_back` within `incident-detection` and workflow configuration.
- Hosted log fetch was not independently available (`gh run view` could not connect); CI facts above are cross-referenced to the sibling log-review note. No tests were run.
