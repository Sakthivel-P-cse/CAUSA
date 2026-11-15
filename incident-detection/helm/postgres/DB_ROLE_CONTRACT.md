# Postgres role contract (C5 anti-circularity)

Dev A owns the shared Postgres and its ground-truth store (`groundtruth.ground_truth`,
contract C5). The **anti-circularity invariant** — the RCA engine must never read a
ground-truth row before its output is scored — is enforced at the **grant level**, not
by convention. The roles and grants live in this chart's `templates/configmap.yaml`
(`init.sql`). A post-install/post-upgrade Helm hook reapplies the RCA restrictions
and grants so upgrades are also safe when Postgres uses an existing PVC.

## Roles (created by init.sql)

| Role | Password (dev) | Can read `groundtruth`? | Intended connector |
|---|---|---|---|
| `causa` | `causa_dev_pass` | yes (superuser owner) | **Dev A only** — trigger writes incidents + ground truth + baselines. Never the engine. |
| `rca_engine` | `rca_engine` | **no** (revoked) | **Dev B** — the Stage-2 RCA engine. `SELECT/INSERT/UPDATE rca_reports`, `SELECT incidents/anomaly_baseline`. |
| `causa_eval` | `causa_eval` | **yes** (SELECT only) | **Dev C** — post-hoc accuracy eval (scoring already happened, so reading truth is fine). `SELECT` on reports/incidents/baselines + `groundtruth.ground_truth`. |

## What Dev B / Dev C must do

Dev A does **not** ship the engine or dashboard charts (those are yours). When you
deploy them, wire the DB connection to the **right role**, not the owner `causa`:

- **Dev B (RCA engine):** connect as `rca_engine`.
  `POSTGRES_USER=rca_engine`, `POSTGRES_PASSWORD=<rca_engine secret>`.
  Connecting as `causa` would bypass every grant and silently break the C5
  anti-circularity guarantee — every accuracy number becomes suspect.

- **Dev C (dashboard / eval):** if the view only shows `rca_reports`/`incidents`,
  `rca_engine` is enough. If it must read ground truth for a post-hoc accuracy
  panel, connect as `causa_eval` (read-only, may read truth) — **never** `causa`.

## Deployment slot

`deploy.sh` installs the real `helm/stage2-rca` chart by default in both environments.
For focused development without the engine, `deploy.sh dev --stage2 mock` explicitly
installs `helm/stage2-mock` into the same Helm release and service slot. Switching the
profile is a normal Helm upgrade and cannot leave both implementations selecting the
`stage2-rca` Service.

## Existing clusters

`init.sql` still runs **once, on an empty data volume**. On every chart install or
upgrade:

1. `postgres-schema-migrate` (hook-weight 1) applies idempotent DDL — tables,
   indexes (`uq_rca_incident_id`), `stage2_outbox`, roles — so upgraded PVCs get
   schema that post-dates the original init.
2. `postgres-rca-engine-grants` (hook-weight 5) creates roles if needed, revokes
   ground-truth access from `rca_engine`, and grants `rca_engine` + `causa_eval`.
   The grants hook fails the Helm operation if required tables are missing.
