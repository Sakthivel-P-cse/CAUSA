# Operator state upgrade

Dashboard acknowledgments and snoozes now use `dashboard_incident_state` when
PostgreSQL is configured. Apply the Postgres Helm schema migration (hook weight 1)
and role grants (weight 5) before rolling out the updated dashboard. Fresh-install
SQL creates the same table. Keep the dashboard root filesystem read-only.
Configured database failures return errors; the dashboard does not silently
switch to local files. Feedback requires PostgreSQL and reports missing rows or
write failures rather than returning success.

Before rollout, stop operator writes and back up `.run/incident_acks.json` from
**every existing dashboard pod/process**. Keep those originals through rollback.
If they contain records, review and import them before starting updated replicas.
From the repository root with existing dashboard dependencies installed and the
destination `CAUSA_PG_DSN` or `PGHOST` configuration set:

```bash
python -m scripts.import_incident_acks /backup/pod-1/incident_acks.json /backup/pod-2/incident_acks.json
# Review the validated record count and resolve every conflict before committing.
python -m scripts.import_incident_acks --apply /backup/pod-1/incident_acks.json /backup/pod-2/incident_acks.json
```

The default command validates files and checks existing database rows without
writing. Apply inserts and verifies rows in one transaction, never overwrites
existing state, and rolls the whole import back on a conflict. Identical records
are idempotent. Conflicting duplicate keys, malformed or timezone-free timestamps,
unknown fields and conflicting database rows are rejected. Compare the validated
count and imported database values with the reviewed backups; retain the inputs.
Do not enable new dashboard replicas if migration or import fails.

When no database is configured, the existing file fallback is for a single local
process only; its writes replace the file atomically. It does not coordinate
multiple replicas. File-mode feedback is unavailable.

## Remediation behavior changes

- Approved Kubernetes plans must match the execution context. Legacy or stale
  plans lacking safe version preconditions require re-proposal.
- Config patches and replica changes carry atomic resourceVersion preconditions;
  inverses refuse intervening conflicting changes. Replica plans do not reapply
  unchanged resource limits, and scale-up never reduces a replica count.
- Post-action observation waits at least one full configured telemetry window.
  Source counter and histogram samples must be at most 30 seconds old, matching
  the chart scrape interval. Missing, stale, partial or non-finite metrics cannot
  establish success. Slower scrape configurations require adjustment before use.
  An unverifiable outcome is stored as failed with an explicit unverified reason
  and requires operator review; missing telemetry alone does not trigger rollback.
- Dashboard remediation success counts all decided plans in the store's current
  statistics window (currently the most recent 500 plans), independently of
  incident service/time filters. The previous `auto_remediation_rate` field stays
  null because automatic/manual provenance is unavailable; consumers use
  `remediation_success_rate`.

Tempo searches now use the persisted incident window for automatic trace
selection. Existing preferred trace IDs still bypass search; Tempo's search
bounds retain integer-second backend precision.
