# Causa improvement implementation — complete

Implemented all seven approved plan items in the current checkout. No commit,
push, deployment, live remediation or production migration was performed.
Pre-existing untracked source and onboarding/scripts were preserved.

## Delivered

1. Kubernetes ConfigMap writes carry atomic resourceVersion preconditions.
   Forward application rejects stale/legacy plans; inverse application preserves
   unrelated edits and refuses a changed flag. Kubernetes adapters reject an
   execution context that differs from the approved plan, including the old
   dependency-rollback fallback path.
2. Scale-up proposals stop at/above the configured cap, reject unknown counts and
   unsupported workload kinds, and correctly handle zero replicas. Application
   uses one conditional replica patch without reapplying resource limits;
   inverse requires the post-apply version and expected replica count. Legacy
   multi-change plans are blocked for re-proposal instead of partially applied.
3. Outcome verification requires finite, complete telemetry and waits a full
   post-action observation window. Freshness checks the oldest contributing
   source series and refuses missing/disappeared contributors. Non-finite values
   serialize as null before PostgreSQL JSONB persistence. Missing telemetry
   produces a durable failed/unverified result without triggering an inverse.
4. Joined incident status and nested remediation statistics contracts are fixed.
   The new remediation_success_rate describes all counted plans, with matching
   frontend labels; auto_remediation_rate remains unavailable without provenance.
5. Relative inbox bounds advance at minute boundaries, preserving explicit
   historical bounds and cleaning up their timer.
6. Acknowledgments/snoozes persist in PostgreSQL with fresh-install schema,
   upgrade migration and grants. Local file writes are atomic and single-process.
   Feedback checks affected rows, persists before success, and exposes missing
   rows/database failures. The checked acknowledgment importer defaults to dry
   run, refuses conflicts and rolls the entire import back on failure.
7. RCA passes exact persisted incident bounds through the reader and scopes
   automatic error-first/fallback trace selection to those bounds. Existing
   recent-search defaults, preferred-ID bypass and cleanup remain.

## Verification

- Final combined Python run:
  `PYTHONPATH=incident-detection:. /tmp/causa-test-venv/bin/python -m pytest -q -rs incident-detection/tests tests`
  — **384 passed, 2 skipped**, exit 0.
- The two existing skips require an explicitly configured RCA report database
  and real node telemetry; see tests/test_infra_handoff.py. The new disposable
  PostgreSQL and Prometheus integrations ran and passed separately and in the
  combined suite.
- PostgreSQL checks cover cross-process persistence, concurrent upserts,
  permission failures, feedback persistence, non-finite SLO JSONB events,
  import dry run/idempotency/conflict rollback and repeatable state-table DDL.
- Real isolated Prometheus checks cover healthy, mixed-age and disappeared
  source series while aggregate SLO values remain numeric.
- Frontend: **7 files / 10 tests passed**, ESLint passed, Vite build passed.
- Both PostgreSQL and dashboard Helm charts rendered; expected state schema,
  grants and readOnlyRootFilesystem remain present. `git diff --check` passed.
- Focused regression failures were observed before fixes; temporary test
  dependencies were installed only in /tmp, without changing dependency manifests.
- Final peer review: [final-review.md](final-review.md), all three review
  findings resolved in code/tooling; reviewer did not independently rerun tests.

## Rollout and evidence limits

Follow [operator-state-upgrade.md](../../docs/operator-state-upgrade.md): apply
schema/grants before dashboard rollout, inventory/back up every old pod's state,
then dry-run and apply the importer where records exist. No import against a
real environment was executed. The current local file contains no records.
An early regression test wrote a test-only acknowledgment into the ignored local
state file; the dashboard agent found only that record and cleared it. Subsequent
file tests use temporary paths.

Kubernetes mutations were verified with mocks and generated patch assertions,
not a live API server. Live Tempo, browser interactions, hosted CI and production
rollout remain untested. Tempo search bounds retain integer-second precision and
preferred IDs intentionally bypass search. SLO source freshness currently assumes
30-second-or-faster scrapes, matching this repository's chart; slower scrape
configurations need adjustment before enabling execution. The remediation rate
uses the store's current most-recent-500-plan statistics independently of incident
service/time filters.

Protocol references used to check the implementation:
[Kubernetes conditional updates](https://kubernetes.io/docs/reference/using-api/api-concepts/),
[Prometheus timestamp functions](https://prometheus.io/docs/prometheus/latest/querying/functions/),
[Prometheus vector matching](https://prometheus.io/docs/prometheus/latest/querying/operators/).

KISS: Repair existing guards, stores and contracts; reuse the current stack and add no service or dependency.
