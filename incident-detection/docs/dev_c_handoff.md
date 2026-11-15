# Stage-2 → Dev C handoff

What the RCA kernel writes, what is verified, and what is not.

## The report surface

Dev C reads `rca_reports` as `causa_eval` (read-only, may also read
`groundtruth.ground_truth`; see `helm/postgres/DB_ROLE_CONTRACT.md`).

| column | contents |
|---|---|
| `root_cause_service` | top-ranked node id, or NULL when the engine abstained |
| `origin_candidates` | ranked candidate objects that could be the **cause**; nodes concluded to be effects (role `victim` / `victim-of-infra`) are excluded. Exactly one per cluster carries `role="origin"` |
| `amplifier_nodes` | node ids with `role="amplifier"` |
| `blast_radius` | node ids the incident **reached** — `anomaly_score >= theta_fire`, minus the origin(s). Not the candidate list |
| `anomaly_scores` | `{node: score}` for every scored node, deliberately unfiltered |
| `confidence_score` | the top candidate's raw confidence. **Uncalibrated** — see below |
| `failure_type` | structural fault class, or NULL when the kernel declined |
| `incident_id` | joins to `incidents` and to `groundtruth.ground_truth` |

Each `origin_candidates` entry carries `node_id`, `node_kind`, `score`,
`confidence`, `role`, `signals` (T/D/P), `dev_vector`, `explanation`,
`cluster_id`, `flags`.

Abstention is a first-class outcome: `root_cause_service` is NULL,
`origin_candidates` is empty, and `root_cause_summary` starts `"RCA abstained:"`.
Do not treat an abstention as a wrong answer — it means the engine found the
incident causally ambiguous, which for near-simultaneous faults is correct.

## Two things that have NEVER run against real infrastructure

Both are covered only by mocks. Check them **first** on deployment; both fail
silently rather than loudly.

**1. The `ON CONFLICT` clause in `insert_report`.**

```sql
ON CONFLICT (incident_id) WHERE incident_id IS NOT NULL DO NOTHING
```

This infers the partial unique index `uq_rca_incident_id`. Every store test uses
a fake cursor, so the syntax has never been parsed by Postgres. If it is wrong,
**every insert raises** and no report is ever written.

```sql
-- verify the index exists and the clause parses
\d+ rca_reports
INSERT INTO rca_reports (alert_name, incident_id) VALUES ('t','dup-test')
  ON CONFLICT (incident_id) WHERE incident_id IS NOT NULL DO NOTHING;
-- run twice; the second must affect 0 rows, not error
```

**2. The node-level PromQL in `prometheus_client`.**

`get_node_cpu_pct_series` / `get_node_memory_pct_series` are respx-mocked only.
A malformed query returns empty and is swallowed by `_range_safe`, so infra
nodes would score 0.0 — **indistinguishable from the bug these methods fixed**
(infra could not be a root cause because it had no metric series).

```bash
# both must return data for a real node name
curl -sG "$PROM/api/v1/query_range" --data-urlencode \
  'query=100 - (avg by (node) (rate(node_cpu_seconds_total{mode="idle",node="<NODE>"}[2m])) * 100)' \
  --data-urlencode "start=$(date -d '-10 min' +%s)" --data-urlencode "end=$(date +%s)" \
  --data-urlencode 'step=60s' | jq '.data.result | length'
```

If node-exporter is not relabeled with a `node` label the client falls back to an
`instance` regex resolved from `kube_node_info`. Confirm `kube_node_info` exposes
`internal_ip`, or infra scoring stays inert.

Smoke test after both: run S5 (`testbed/chaos/S5_node_stress.yaml`) and confirm
the report names the node, with `failure_type = 'Infra'`.

## Known gaps

- **`confidence_score` is uncalibrated.** It is a rank-margin, monotone
  non-increasing with rank, in [0,1] — but not a probability. Isotonic/ECE
  calibration is Dev C's.
- **`failure_type` coverage is partial.** Only `Infra`, `Dependency`, and
  `Resource` are assigned, from structure (see `scoring/fault_class.py`).
  `AppLogic` / `Config` / `Data` need evidence the kernel does not gather and are
  left NULL. NULL means "not classified", never "no fault".
  One known miss: an *instrumented* service can be a `Dependency` **fault**
  while not being a `NodeKind.DEPENDENCY` **node** — the rule only catches the
  latter, so a slow-but-instrumented dependency classifies as NULL.
- **Offline accuracy numbers do not transfer.** `testbed/harness/evaluate.py`
  scores abstract kernel fixtures, not the chaos scenarios whose IDs they share
  (`--check-catalog` prints the divergence). It detects regressions; it says
  nothing about real-incident accuracy. The corpus is also saturated — every
  weighting including T scores 1.00 — so it cannot guide tuning.
- **S3 (clock skew) and S4 (Kafka shared dependency)** have chaos CRDs but no
  fixture, so those paths are unexercised offline.
- **Untuned constants.** The T/D/P weights (equal thirds), role thresholds
  (0.55/0.45/0.35), `ALPHA=0.5`, `D_PRIOR=1.0`, and `kappa=3.0` are reasoned
  defaults, not fitted values. Fitting them needs real chaos runs joined to
  ground truth — which is the `causa_eval` lane.
