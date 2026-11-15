# Dev C integration — open issues & handoff

Status of the Dev C ← Dev A+B integration on `feature/dev-abc-integration`, and
the concrete work still owed. This is the single checklist to work from.

- **Integration itself:** verified. See `tests/test_integration_abc.py` — the real
  A+B kernel output flows into every Dev C consumer (classify, remediation, eval)
  only through `contracts/` + `adapters/`, and an architectural guard fails if any
  Dev C lane imports a B/A internal directly.
- **What is NOT verified:** two code paths have never run against real
  infrastructure, plus four known gaps that need the chaos corpus. Details below.

Related docs: [`dev_c_handoff.md`](dev_c_handoff.md) (report surface + narrative),
[`rca_kernel.md`](rca_kernel.md), [`scorer_c4.md`](scorer_c4.md).

---

## Blockers — never run against real infra (fail *silently*)

Both are covered only by mocks and fail silently rather than loudly, so check them
**first** on deployment. Each has an executable target in
[`tests/test_infra_handoff.py`](../tests/test_infra_handoff.py) that is **skipped
until you point it at real infra** — a PASS with the env var set is the proof.

### 1. Postgres `ON CONFLICT` clause in `rca.store.insert_report`

```sql
ON CONFLICT (incident_id) WHERE incident_id IS NOT NULL DO NOTHING
```

This infers the partial unique index `uq_rca_incident_id`. Every store unit test
uses a fake cursor, so the syntax has **never been parsed by Postgres**. If the
index is missing or the clause is malformed, **every insert raises and no report
is ever written**.

- **Owner:** Dev B (store/schema)
- **Verify:** load the `rca_reports` schema (incl. `uq_rca_incident_id`) into a
  throwaway DB, then:
  ```bash
  CAUSA_TEST_PG_DSN=postgresql://user@localhost/causa_test \
    pytest tests/test_infra_handoff.py::test_on_conflict_clause_parses_and_is_idempotent
  ```
  Green = the clause parses and the second insert affects 0 rows (not an error).

### 2. Node-level PromQL in `PrometheusClient`

`get_node_cpu_pct_series` / `get_node_memory_pct_series` are respx-mocked only.
A malformed query returns `[]` and is swallowed by `_range_safe`, so infra nodes
score `0.0` — **indistinguishable from the original bug these methods fixed**
(infra could never be a root cause because it had no metric series).

If node-exporter has no `node` label, the client falls back to an `instance`
regex resolved from `kube_node_info.internal_ip`. Confirm `kube_node_info`
exposes `internal_ip`, or infra scoring stays inert.

- **Owner:** Dev A (ingest/clients)
- **Verify:**
  ```bash
  PROMETHEUS_URL=http://localhost:9090 CAUSA_TEST_NODE=<real-node-name> \
    pytest tests/test_infra_handoff.py::test_node_cpu_and_memory_series_are_nonempty
  ```
  Green = both series are non-empty for a real node.
- **End-to-end smoke:** run `testbed/chaos/S5_node_stress.yaml` and confirm the
  report names the node with `failure_type = 'Infra'`.

---

## Known gaps — need the chaos corpus + ground truth

Not unit-testable offline; tracked here so they are not forgotten. From
[`dev_c_handoff.md`](dev_c_handoff.md) "Known gaps".

### 3. `confidence_score` is uncalibrated
A rank-margin, monotone non-increasing with rank, in `[0,1]` — but **not a
probability**. Isotonic / ECE calibration is **Dev C's** lane.

### 4. `failure_type` coverage is partial
Only `Infra`, `Dependency`, and `Resource` are assigned, from structure
(`scoring/fault_class.py`). `AppLogic` / `Config` / `Data` need evidence the
kernel does not gather and are left **NULL** — NULL means "not classified",
never "no fault". Known miss: an *instrumented* service can be a `Dependency`
**fault** while not being a `NodeKind.DEPENDENCY` **node**; the rule only catches
the latter, so a slow-but-instrumented dependency classifies as NULL.

### 5. S3 / S4 have no offline fixture
S3 (clock skew) and S4 (Kafka shared dependency) have chaos CRDs but no fixture,
so those paths are **unexercised offline**.

### 6. Untuned constants
T/D/P weights (equal thirds), role thresholds (`0.55 / 0.45 / 0.35`),
`ALPHA=0.5`, `D_PRIOR=1.0`, and `kappa=3.0` are reasoned defaults, **not fitted
values**. Fitting them needs real chaos runs joined to ground truth — the
`causa_eval` lane. Note the offline harness corpus is saturated (every weighting
including T scores 1.00), so it cannot guide tuning.

---

## Checklist

| # | Issue | Owner | Proof of done |
|---|---|---|---|
| 1 | `ON CONFLICT` clause parses on real Postgres | Dev B | `test_on_conflict_clause_parses_and_is_idempotent` passes |
| 2 | Node PromQL returns real series | Dev A | `test_node_cpu_and_memory_series_are_nonempty` passes + S5 smoke |
| 3 | Calibrate `confidence_score` | Dev C | ECE / isotonic fit on chaos corpus |
| 4 | Extend `failure_type` to AppLogic/Config/Data | Dev B/C | classifier evidence + non-NULL on corpus |
| 5 | Add S3 / S4 offline fixtures | Dev B | fixtures exercise both paths |
| 6 | Fit T/D/P weights & thresholds | Dev C | tuned values from chaos runs vs ground truth |
