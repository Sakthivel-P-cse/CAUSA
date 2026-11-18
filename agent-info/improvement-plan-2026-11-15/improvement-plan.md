# Causa improvement plan

Status: implemented and locally verified; see [completion report](../implementation-2026-11-15/completion-report.md). The original planning baseline and checks below are historical. Baseline: current checkout, including pre-existing untracked work. The first hunt's findings are inputs, not automatic proof; fresh source review and three isolated Python/mock probes confirm the remediation issues below. No live service was contacted by those probes.

## Recommended order

| Order | Task | Dependency | Observable result |
|---|---|---|---|
| 1 | Atomic Kubernetes state checks | None | Concurrent changes are preserved; stale plans fail before mutation |
| 2 | Monotonic, bounded scale-up | 1 for shared apply file ownership | A scale-up never decreases replicas or overwrites unchanged resources |
| 3 | Truthful remediation outcome verification | 1–2 before integration | Missing/non-finite telemetry never produces verified success |
| 4 | Dashboard status and statistics contracts | None | Resolved rows stay resolved; success-rate counts use the store's real shape |
| 5 | Moving relative inbox windows | None | An open inbox includes new incidents; historical bounds stay fixed |
| 6 | Durable operator state and truthful feedback | 4; schema before API rollout | Acks/snoozes survive restart and writes fail visibly when persistence fails |
| 7 | Exact historical telemetry windows and trace selection | Reader bounds first, then Tempo selection | RCA evidence uses the persisted incident window |

Tasks 1–3 are one owner because they share remediation files. Task 6 includes feedback because both change persistence/error contracts in dashboard/backend/db.py. Dashboard tasks touching that file must run serially. Pipeline work can run independently once its reproduction is established.

## 1. Make remediation preconditions atomic — High

Evidence: incident-detection/remediation/apply/k8s.py:149–201 reads resourceVersion, compares locally, then sends a whole-document merge patch without metadata.resourceVersion. The isolated mock probe confirmed that the outgoing patch has no atomic precondition. A writer between read and patch can lose its update.

Reach: build_flagd_patch_plan → apply_config_patch / rollback_config_patch → apply_exact_flagd_patch; worker._dispatch_execute/_dispatch_rollback; adapter registry and typed stored plans. Review forward and inverse paths together.

Smallest direction: use the existing kubectl path with a server-enforced version precondition. A stale plan must fail, not silently rebase and retry. For inverse application, verify that the flag still has the value installed by this plan, then condition the inverse on the live version; preserve unrelated edits. Keep current result/error shape. Do not introduce a Kubernetes SDK or generic transaction framework. Check that the approved target/context matches the execution context; reject mismatches rather than selecting a different target silently.

Acceptance: an update between GET and PATCH causes conflict without clobbering it; a pre-existing stale plan makes no mutation; a normal patch succeeds; inverse preserves unrelated edits and refuses to undo a newer same-flag change; denied target/context makes no kubectl mutation. Ensure stored legacy plans without necessary safe preconditions are blocked with a re-propose message.

Checks: add focused adapter tests under incident-detection/tests/test_remediation_real.py or a dedicated test_remediation_apply_safety.py; mock kubectl for unit coverage, then validate the precondition against a disposable local ConfigMap when a test cluster is available. Do not apply to live application ConfigMaps.

## 2. Make scale-up monotonic and avoid unnecessary resource writes — High

Evidence: remediation/branches/stubs.py computes min(MAX_REPLICAS,current+1). A fresh probe reproduced 8 → 5 at the default cap. apply_resource_patch also reapplies the captured resources even when the proposed action only changes replicas.

Reach: propose_resource_scale, read_workload_revision, TypedPlanSpec change/inverse, apply_resource_patch and its rollback adapter; worker result handling.

Smallest direction: at/above the cap, return the existing non-executable/runbook result; below it propose current+1, bounded by the cap. Reject unavailable/invalid replica counts rather than guessing one. A replica-only proposal should change only replicas. Enforce a current-state precondition at apply time, including the inverse, so a later HPA/operator scale is not overwritten. If a stored plan contains several mutations and partially succeeds, record precisely what changed and handle only safe conditional compensation; never report an unmodified workload or blindly restore old state.

Acceptance: below-cap increments once; at/above-cap is non-executable; invalid counts are rejected; newer live state prevents stale apply/inverse; no unchanged resources patch is emitted; partial failure is surfaced with actual operations and safe recovery details.

Checks: focused proposal and adapter tests with current counts 0, cap−1, cap, cap+3 and unknown; use mocked Kubernetes reads/writes. Confirm supported workload kinds before claiming Rollout scaling support; unsupported kinds must fail explicitly.

## 3. Require evidence for remediation success — High

Evidence: SLO gates skip None and plans for resource/infra/data branches disable require_fresh. The fresh probe confirmed an entirely missing snapshot passes when require_fresh=False. This is a truthful-outcome problem even if permissive preflight execution was intentional.

Reach: remediation/slo.py snapshot_service/evaluate_gates; worker.execute_plan/_observe; SLOGate defaults and generated plans in branches/stubs.py; existing terminal-status UI/store consumers.

Smallest direction: distinguish permission to attempt a repair from evidence that it worked. Reuse current failed/result-reason contracts for unverifiable outcomes rather than adding a new persisted status unless required. Require finite, sufficient measurements for configured post-action gates, honor the intended observation window, and preserve the deliberate code_fix_pr meaning (PR creation success does not mean deployment success). Missing telemetry alone must not trigger an unsafe inverse or a claim of successful rollback; retain mutation details for operator recovery. Keep automation defaults and calibration gates unchanged.

Acceptance: absent, partial, stale, NaN and infinite samples cannot yield verified success; finite healthy data passes; threshold breach follows the existing safe rollback path; an irreversible action never claims successful rollback; unknown post-action outcome is explicit and durable; configured observation window reaches the Prometheus query. Test preflight and postflight separately.

Checks: extend test_remediation_real.py and test_worker_rollback_safety.py with mocked snapshots/dispatch/store; check test_remediation_service.py callers. No live remediation during automated checks.

## 4. Correct dashboard read contracts — Medium

Evidence: prior reviewer reproduced joined status='resolved' being normalized to open; fresh source review must retain existing status precedence. Store.stats returns counts under by_status while the stats router reads top-level counts.

Reach: dashboard/backend/db.py list/detail normalization; incident consumers/filters; routers/stats.py; remediation_store.stats; frontend summary consumers and API fixtures.

Smallest direction: preserve the authoritative joined incident status with explicit fallback for legacy file rows; read status counts from the existing nested structure. Confirm the metric's meaning before calling all remediation outcomes automatic: if no auto/manual provenance is stored, label the metric accurately or keep it unavailable rather than inventing attribution.

Acceptance: resolved/open/legacy rows normalize consistently in list and detail; missing timestamps do not reopen a resolved row; succeeded/failed/zero-count cases have the documented denominator; API and frontend fixtures agree.

## 5. Keep relative inbox windows current — High

Evidence: dashboard/frontend/src/pages/IncidentInbox.jsx captures Date.now once in state and memoizes relative bounds. A refetch repeats the same server-side filter after the initial minute.

Reach: IncidentInbox timeWindow/query; existing stream invalidation/refetch hooks; shared filters, pagination and selection behavior; frontend tests.

Smallest direction: advance relative time using the existing refresh lifecycle, or one cleaned-up minute tick if the lifecycle offers no suitable clock. Keep query bounds stable within a minute to avoid request churn. Explicit from/to historical windows remain unchanged. Preserve user filters and reset page/selection only when their current contract requires it.

Acceptance: fake-clock test crossing a minute includes a newly arriving incident; historical window does not move; reconnect/refetch uses current bounds; unmount cleans subscriptions/timers; no render/refetch loop.

## 6. Persist operator state and report write failures — Medium

Evidence: ack_store.py writes /app/.run/incident_acks.json while the dashboard Helm deployment uses readOnlyRootFilesystem and no writable state volume. A writable emptyDir would avoid permissions errors but still lose state on pod replacement. The dashboard reviewer also source-confirmed that feedback writes suppress PostgreSQL failures, do not detect unmatched IDs and fabricate success in file mode.

Reach: ack_store.py, db.py persistence initialization, incidents router ack/snooze/feedback endpoints, schema/migrations, Helm database-role permissions and dashboard actions. Use existing DashboardStore psycopg connection conventions (there is no dashboard connection pool). Migration surfaces: incident-detection/helm/postgres/templates/configmap.yaml, schema-migrate-job.yaml and rca-engine-grants-job.yaml. Apply matching table/grant changes to fresh installs and upgrades; keep incident list/stat merges efficient via bulk state reads rather than one DB connection per item.

Smallest direction: store acknowledgments and snoozes using the existing Postgres path. Keep incident identity/scope consistent with existing schema keys. An explicitly supported single-process development fallback may remain, with durable behavior and visible errors; do not silently fall back after a configured production DB failure. Preserve API response shapes where possible. Missing incident/report returns not-found, storage failure returns an error, and success is returned only after a committed write. Include the confirmed feedback error path; use affected-row checks and explicit persistence failure responses. Preserve existing file acknowledgments before rollout: retain the source file, inspect record count and provide a one-time checked import if existing records need migration; never silently discard operator state.

Acceptance: ack/snooze survives new process/pod and is visible across replicas; snooze expiry and clearing behave correctly; absent rows and denied DB writes do not return success; read-only-root deployment can write operator state without loosening filesystem security. Feedback persists and roundtrips in supported modes. Migration applies before API rollout; rollback leaves existing rows intact.

Checks: backend focused tests plus local Postgres integration with separate writer connections; Helm rendering verifies root remains read-only and required DB permissions are present. UI tests cover visible error handling. No production migration during this planning task.

## 7. Preserve exact incident windows and use them for trace selection — High/Medium

The pipeline reviewer source-confirmed both mechanisms; runtime reproduction remains an implementation prerequisite. orchestrator.py:219–239 converts persisted bounds into whole-minute lookback; reader.py:117–120 reconstructs them. The end is preserved, but a 90-second window loses its first 30 seconds, while a sub-minute window expands backward. reader.resolve_trace_id instead searches relative to now through tempo_client.py:112,132, and reader.py:118–144 can prepend that trace to a historical result.

Reach: ingest/reader.py, ingest/clients/tempo_client.py, rca/orchestrator.py, trigger/app.py, rca/adapter.py offset consumers; tests/rca/test_orchestrator.py, test_trigger_service.py, test_client_hardening.py and test_adapters_http.py. No schema change is indicated.

Smallest direction: first accept optional paired exact window bounds in the existing reader, preserving around/lookback defaults; have the orchestrator pass persisted bounds directly. Then use that same Window for automatic error-first and fallback Tempo search. Preserve existing recent-search defaults for other callers and preferred-ID behavior; whether a preferred ID should be constrained is a separate policy question, not a blocker for automatic selection. Retain validation, exception behavior and reader cleanup. Tempo's integer-second timestamp serialization remains a documented backend precision limit.

Acceptance: whole-minute, 90-second and sub-minute windows preserve requested bounds internally; missing/nonpositive/partial bounds are rejected; a delayed incident with no preferred ID selects in-window evidence and does not prepend a current unrelated trace; error-first/fallback/no-result/preferred-ID paths retain their contract; reader closes on success and failure. Mock assertions check the exact submitted bounds; a later local Tempo test establishes actual timestamp precision. Implement exact reader bounds before trace search changes so both use one source of truth.

## Verification and delivery gates

Before implementation, establish a working test interpreter using existing project dependency instructions. The first hunt found pytest missing in root .venv; no dependency was installed for this plan. A missing runner is an environment gap, not a passing test. Observe each regression failing for its intended reason, make the minimal fix, then run only affected tests before integration.

Expected focused commands after the test environment is ready:

- PYTHONPATH=incident-detection:. <test-python> -m pytest -q incident-detection/tests/test_remediation_real.py incident-detection/tests/test_worker_rollback_safety.py incident-detection/tests/test_remediation_service.py
- Add the new adapter/backend/pipeline regression test paths to that command as those files are created.
- cd dashboard/frontend && npm test && npm run lint && npm run build (after dashboard changes).
- Disposable local Postgres and Kubernetes checks are separate evidence gates; unit tests do not substitute for them.

Keep changes in independently reviewable batches: safety 1–3 (shared files), dashboard 4, inbox 5, persistence 6, verified pipeline 7. Update safety/setup docs only for changed behavior. No commit, push, deployment, live remediation, auth redesign, new service, new dependency, or unrelated refactor is part of this plan. Budget estimates describe agent review effort, not implementation duration or guaranteed billing.

## Planning-stage checks (historical)

Three safe stdlib/mock assertions passed: scale-up proposal 8→5 reproduced; missing telemetry passed require_fresh=False; emitted ConfigMap merge patch lacked a resourceVersion precondition. Both reviewer plans were saved and read by the master: [dashboard-plan.md](dashboard-plan.md) and [pipeline-plan.md](pipeline-plan.md). Source paths were reviewed. No application tests, browser, live Postgres or Kubernetes validation were run for this plan.

KISS: Repair existing guards, response contracts and persistence using the current stack; add no service or framework.
