# Postgres role contract (C5 anti-circularity)

Dev A owns the shared Postgres and its ground-truth store (`groundtruth.ground_truth`,
contract C5). The **anti-circularity invariant** — the RCA engine must never read a
ground-truth row before its output is scored — is enforced at the **grant level**, not
by convention. The roles and grants live in this chart's `templates/configmap.yaml`
(`init.sql`) and are created on first startup of an empty data volume.

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

Dev A's `deploy.sh` deploys the observability stack + Postgres + the ingest trigger,
and in **dev** stands up `helm/stage2-mock` (a trivial 200-returning receiver at the
`stage2-rca` service) so the trigger's forward path is testable end-to-end. Deploy
your real chart into the **`stage2-rca` namespace with a `stage2-rca` Service** and it
swaps in cleanly (uninstall `stage2-mock` first). In **prod**, `deploy.sh` deploys no
mock — the ingest trigger buffers forwards to the durable `stage2_outbox` table until
your engine is reachable, so nothing is lost in the gap.

## Existing clusters

`init.sql` runs **once, on an empty data volume**. On a cluster whose Postgres volume
already exists, the roles/grants above must be applied **by hand** (run the
`CREATE ROLE` / `REVOKE` / `GRANT` block from `init.sql`).
