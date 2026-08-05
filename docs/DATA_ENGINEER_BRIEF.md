# Causa — Technical Brief for Data Engineers

**Audience:** Data engineers evaluating how Causa ingests, models, stores, labels, and scores observability data  
**Companion brief:** [`CLOUD_ENGINEER_BRIEF.md`](CLOUD_ENGINEER_BRIEF.md) (infra / deploy / security)  
**Status:** Development and controlled evaluation — labeled corpus + calibration gates block production auto-remediation

---

## 1. One-sentence pitch (data lens)

Causa is an **incident-scoped data pipeline**: it normalizes multi-signal observability (metrics, traces, logs, topology) into typed contracts, scores anomalies against robust baselines, materializes an influence graph, and writes ranked root-cause records — with a separate labeled ground-truth schema for offline evaluation and confidence calibration.

---

## 2. Why a data engineer should care

| DE concern | How Causa maps |
|---|---|
| Multi-source join | Prometheus + Tempo + Loki + K8s topology joined on an **incident time window** + service identity |
| Schema contracts | Stdlib-only `shared/contracts/` (C1 telemetry, C2 anomaly, C3/C4 RCA, C5 ground truth) |
| Feature store analogue | `anomaly_baseline` (median/MAD per node×metric) + optional fitted weights/calibration artifact |
| Lineage / provenance | Raw payloads stay in backends; Postgres keeps **`TelemetryReference` pointers**, not copies |
| Train/serve discipline | Engine role **cannot read** `groundtruth.*`; labels are out-of-band |
| Quality gates | Corpus pipeline fail-closed on missing provenance, stratum coverage, holdout size |
| Serving table | `rca_reports` + `incidents` power the product API (not ad-hoc notebook exports) |

Think of Causa less as “another dashboard” and more as:

> **streaming trigger → batch-window extract → transform to graph features → score → load ranked fact + optional remediation workflow**

---

## 3. End-to-end data flow

```text
┌───────────────┐   scrape/OTLP    ┌──────────────────────────────┐
│ App services  │ ───────────────► │ Tempo / Prometheus / Loki    │
└───────────────┘                  │ (+ MinIO object blocks)      │
                                   └──────────────┬───────────────┘
                                                  │ alert fires
                                                  ▼
                                   ┌──────────────────────────────┐
                                   │ Alertmanager webhook payload │
                                   └──────────────┬───────────────┘
                                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ causa-ingest (Stage-1)                                                   │
│  • open incident row                                                     │
│  • define Window [t0 − lookback, t0]                                     │
│  • EXTRACT via TelemetryAdapter: traces / metrics / logs / topology      │
│  • scope services (allow/deny regex, profile)                            │
│  • store telemetry_refs JSONB + durable stage2_outbox                    │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ stage2-rca (Stage-2)                                                     │
│  • TRANSFORM: AnomalyOutput vs anomaly_baseline                          │
│  • build 4 graphs → Influence Graph                                      │
│  • score T/D/P → RankedResult                                            │
│  • LOAD: rca_reports (idempotent on incident_id)                         │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    ▼
┌──────────────────────┐     ┌────────────────────────────────────────────┐
│ Product / operators  │     │ Eval lane (offline / batch)                 │
│ dashboard reads      │     │ groundtruth + labeled JSONL → fit/calibrate │
│ rca_reports          │     │ → fit_result.json (weights + cal map)       │
└──────────────────────┘     └────────────────────────────────────────────┘
```

**Timestamps:** all contract datetimes are **timezone-aware UTC**. Naive timestamps are treated as bugs (they break clock-skew scenarios and windowing).

---

## 4. Canonical contracts (the data model)

All cross-component types live in `shared/contracts/` — **stdlib only**, no client or DB imports. Producers and consumers meet at the contract, not at each other’s packages.

### C1 — Telemetry (`contracts/telemetry.py`)

Signal types stay **distinct** (spans ≠ metric points ≠ logs):

| Type | Role |
|---|---|
| `Window` | Closed UTC interval for queries |
| `SpanEnvelope` / `TraceSpan` | Tempo spans + kind/attributes + causal ordering metadata |
| `MetricPointEnvelope` | Named metric series for a service |
| `LogRecordEnvelope` / `LogRecord` | Loki records for classification evidence |
| `ServiceEdge` / `Edge` | Call topology with `call_type ∈ {sync, async, runs-on}` |
| `InfraNode` | Node/pod/container resource state |
| `IncidentTelemetry` | Bundle for one incident window |
| `TelemetryReference` | Pointer into Tempo/Prometheus/Loki (`backend`, `kind`, `locator`, window) |

**Ordering hierarchy for spans:** `trace_dag` → `logical_clock` → `wall_clock` → `fallback` (`OrderingSource`).

**Edge semantics (critical for downstream graph math):**

| `call_type` | Influence orientation in RCA |
|---|---|
| `sync` (HTTP/gRPC/DB) | Reversed in Influence Graph (callee → caller) |
| `async` (messaging) | Kept (producer → consumer) |
| `runs-on` | Infra → service |

Service naming is configurable via label-key order (`CAUSA_SERVICE_NAME_LABEL_KEYS`) and monitored/excluded regexes — this is the DE equivalent of a **entity resolution / dimension table** problem.

### C2 — Anomaly (`contracts/anomaly.py`)

Per-node deviation primitives that all three scoring signals sit on:

| Field | Meaning |
|---|---|
| `Baseline` | Robust `median`, scaled `mad` (≈σ), sample `n` for one `(node, metric)` |
| `AnomalyOutput` | `anomaly_score` (max over metrics), `dev_vector`, `first_deviation` |
| Metrics scored | `p95_latency`, `error_rate`, `cpu`, `retry_rate`, `saturation` |

Anomaly is **one-sided** (increases only). Missing metrics are omitted, never fabricated. `first_deviation = None` is a valid slow-burn outcome — do not backfill.

Threshold knobs: `θ_fire`, `k_persist`, `θ_low`, `κ` (squash).

### C3/C4 — RCA output (`contracts/rca.py`)

| Type | Meaning |
|---|---|
| `NodeKind` | `service` / `dependency` / `infra` / `span` (structural, not name-based) |
| `Role` | `origin`, `amplifier`, `victim`, `victim-of-infra`, … |
| `SignalScores` | `T`, `D`, `P` ∈ [0,1]-ish continuous scores |
| `RootCauseCandidate` | node + score + confidence + role + explanation + `dev_vector` |
| `RankedResult` | Full ranked list / clusters / abstention flags |

Raw confidence (pre-calibration):

```text
raw_conf = S(top) · (S(top) − S(second)) / (S(top) + ε)
```

This is a **margin**, not a probability, until isotonic calibration is fitted.

### C5 — Ground truth (`contracts/ground_truth.py`)

| Type | Meaning |
|---|---|
| `Incident` | One chaos/experiment label: `true_root_node`, `fault_class`, inject interval, optional scenario/subgraph |
| `FaultClass` | AppLogic, Config, Infra, Dependency, Resource, Data (+ Timing for S3, excluded from classifier grading) |
| `ScenarioId` | S0–S5, D1–D3 synthetic/chaos scenarios |

**Anti-leakage invariant:** the RCA engine must never read ground truth before scoring. Enforced by Postgres grants (`rca_engine` has no `SELECT` on `groundtruth`).

---

## 5. Extract layer (ingest as ETL)

Package: `incidents/ingest/`

| Surface | What it returns |
|---|---|
| `TelemetryAdapter` | Fine-grained C1 ports: `traces(win)`, `metrics(svc, name, win)`, `logs(svc, win)`, `topology(win)` |
| `TelemetryReader.read_incident_window(...)` | One-shot `IncidentTelemetry` for an alerted service + lookback |
| `ingest/trigger` | HTTP webhook: Alertmanager → open incident → snapshot → outbox → Stage-2 |

**What is persisted at Stage-1 vs re-fetched:**

- **Persisted:** incident metadata, window bounds, `telemetry_refs` JSONB, outbox payload
- **Not copied wholesale:** raw Tempo blocks / Prom series / Loki lines stay in backends

That design keeps the operational DB small and makes reprocessing possible by replaying references over the same window.

**Scoping (dimension filters):**

- `CAUSA_APP_NAMESPACE`
- `CAUSA_MONITORED_SERVICES_REGEX` / `CAUSA_EXCLUDED_SERVICES_REGEX`
- Profile files under `shared/profiles/` (e.g. `otel-demo`)

Without allowlists, the graph fills with infrastructure noise — same class of problem as joining an unfiltered clickstream to every CDN edge.

---

## 6. Transform layer (features → graph → scores)

### 6.1 Baselines (feature store lite)

Table: `anomaly_baseline`

| Column | Type | Notes |
|---|---|---|
| `node` | TEXT | Service / infra entity |
| `metric` | TEXT | From `METRICS` tuple |
| `median` | FLOAT | Robust center |
| `mad` | FLOAT | 1.4826 × MAD, floored |
| `n` | INT | Sample size |
| `captured_at` | TIMESTAMPTZ | Capture provenance |

**Capture discipline:** baselines must be taken with chaos/faults **off**. Contaminated baselines poison T, D, and P simultaneously.

### 6.2 Graph materialization (ephemeral per incident)

Four logical graphs → one Influence Graph. These are compute artifacts for scoring; the durable product is the ranked report (plus optional evidence JSONB), not a permanent graph warehouse — though a DE could export IG edges if needed for analytics.

### 6.3 Scoring formula

```text
S(N) = w_T·T(N) + w_D·D(N) + w_P·P(N)
```

Default weights: equal thirds until `fit_result.json` (or `RCA_WEIGHTS_ARTIFACT`) is loaded.

| Signal | Data inputs |
|---|---|
| **T** | `first_deviation` times across subgraph |
| **D** | Out/in influence = Σ anomaly of successors/predecessors on IG |
| **P** | Temporal order consistency on `edge.degraded` edges |

Post-score **layer arbitration** can promote infra over hosted services when infra leads on time + anomaly (`victim-of-infra`).

---

## 7. Load layer — Postgres warehouse schema

System of record for product + eval (not the high-volume telemetry lake — that’s Tempo/Loki/Mimir/MinIO).

### 7.1 Core tables

**`incidents`** — fact of “an alert opened a window”

| Column | Notes |
|---|---|
| `incident_id` | PK |
| `alert_name`, `alerted_service`, `namespace`, `severity` | Alert dimensions |
| `window_start`, `window_end` | Query bounds |
| `telemetry_refs` | JSONB references into backends |
| `status`, `snapshot_status` | Pipeline progress |
| `triggered_at`, `resolved_at` | Lifecycle |

**`rca_reports`** — ranked diagnosis fact

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `incident_id` | Unique when present (partial unique index) |
| `alert_name`, `alerted_service` | Join keys back to alert |
| `root_cause_service`, `failure_type`, `confidence_score` | Headline fields |
| `origin_candidates`, `amplifier_nodes`, `blast_radius` | JSONB arrays |
| `anomaly_scores`, `evidence`, `timeline`, `inference_evidence` | JSONB explainability |
| `feedback_verdict`, `feedback_notes` | Operator label feedback loop |

**`stage2_outbox`** — durable messaging between ingest and RCA

| Column | Notes |
|---|---|
| `payload` | JSONB |
| `attempts`, `last_error` | Retry bookkeeping |
| `sent_at` | NULL = pending (partial index) |

**`groundtruth.ground_truth`** — labeled dataset (eval only)

| Column | Notes |
|---|---|
| `incident_id` | PK, joins to incidents/reports |
| `true_root_node`, `fault_class` | Labels |
| `inject_start`, `inject_end` | Experiment interval |
| `topology_id`, `labeler`, `verified_at`, `label_notes` | Provenance (required for release corpus) |

**`remediation_plans` / `remediation_events`** — workflow state machine + audit

Statuses: `proposed → approved → claimed → executing → observing → succeeded|failed|rolled_back` (also `rejected`, `expired`). Leases via `claimed_by` / `lease_until` for exactly-once-ish worker claiming (`SKIP LOCKED`).

### 7.2 Roles (data access control)

| Role | Access intent |
|---|---|
| `rca_engine` | Write incidents / RCA path |
| `causa_remediator` | Own remediation tables |
| `causa_eval` | Read-only eval / joins including ground truth |

This is intentional **train/serve separation** at the database layer.

---

## 8. Offline / batch data products (`eval/`)

| Module / artifact | Purpose |
|---|---|
| Synthetic fixtures S0–S5, D1–D3 | Unit-level graph/scoring correctness |
| Golden OTel fixture | Contract smoke (`n=1` — not a portfolio claim) |
| `eval/baselines/` | Captured baseline JSON (+ DB upsert) |
| `eval/fit_from_live.py` | Fit T/D/P weights from labeled live rows |
| `eval/calibration.py` | Reliability bins, ECE, isotonic map |
| `eval/corpus_pipeline.py` | Release gate: stratified split + min coverage |
| `eval/artifacts/fit_result.json` | Versioned weights + calibration map |

### Corpus pipeline (release dataset)

Input: operator-confirmed JSONL rows with:

- `incident_id`, `true_root_node`, `fault_class`, `alert_name`, `topology_id`
- `origin_candidates` (engine output at label time)
- `labeler`, `verified_at` (provenance)

Behavior:

1. Validate rows (fail on missing fields / dupes)
2. Stratify by `fault_class|alert_name|topology_id`
3. Split train / calibration / holdout (deterministic seed)
4. Fit weights + calibration
5. Emit versioned artifact **only if** each stratum meets minimum calibration (≥30) and holdout (≥10) counts

```bash
python -m eval.corpus_pipeline \
  --input eval/corpus/labeled-incidents.jsonl \
  --out eval/artifacts/fit_result.json
```

Dataset hash (`sha256` of canonical JSON) is part of release hygiene — treat weights + baselines + code commit as **one versioned unit**.

### Metrics a DE/ML engineer will recognize

- Ranking: top-1, top-3, MRR
- Abstention precision/recall
- Calibration: ECE + reliability diagram
- Classification: confusion matrix over fault classes (Timing excluded)
- Isolation: multi-fault cluster counts on D1–D3

---

## 9. Hot path vs cold path storage

| Store | Volume character | Contents |
|---|---|---|
| Tempo / Loki / Prometheus→Mimir (+ MinIO/S3) | High volume, retention-driven | Raw/near-raw telemetry lake |
| Postgres | Low volume, high value | Incidents, reports, baselines, labels, remediation audit |
| `fit_result.json` | Tiny | Model hyperparameters + calibration map |
| Dashboard API | Serving layer | Reads Postgres (+ live backend queries for drill-down) |

Retention policy for the lake is an ops concern; for the warehouse, treat `rca_reports` + `groundtruth` + `remediation_events` as the long-lived analytical assets.

---

## 10. Data quality failure modes (practical)

| Symptom | Likely data cause |
|---|---|
| Always abstains / near-zero confidence | Contaminated baselines; unfitted weights; candidates clustered |
| Wrong service named | Entity naming / label-key mismatch; scope regex too wide/narrow |
| Infra never wins | Empty node-exporter / `kube_pod_info` series → infra anomaly ≈ 0 |
| Cascades ranked as origins | Sync edges missing `call_type` → Influence Graph mis-oriented |
| Eval looks perfect offline, weak live | Golden `n=1` smoke ≠ stratified corpus; labels missing provenance |
| Auto-remediation refused | By design until non-constant calibration map + sample floors exist |

---

## 11. How a data engineer would extend Causa

Reasonable extension points:

1. **Export pipeline** — CDC or scheduled dump of `rca_reports` ⊕ `groundtruth` ⊕ feedback columns into your warehouse (BigQuery/Snowflake/Redshift) for MTTR analytics
2. **Entity resolution** — harden service identity mapping (K8s labels ↔ OTel `service.name` ↔ Prom labels)
3. **Baseline jobs** — scheduled clean-window capture with data-quality checks (`n` floors, drift alerts on MAD)
4. **Feature expansion** — new metrics in `METRICS` + baseline rows (must version with scorer)
5. **Labeling UI → JSONL** — operator `feedback_verdict` promotion into corpus rows with `labeler`/`verified_at`
6. **Lakehouse** — replace MinIO with cloud object storage; keep contracts stable so Stage-1/2 code does not care

Avoid:

- Hand-editing `fit_result.json` for demos that will be mistaken for production evidence
- Letting the ranking service read ground truth
- Collapsing spans/metrics/logs into one undifferentiated “events” table at the contract boundary

---

## 12. Suggested DE evaluation plan

1. Deploy reference stack; confirm rows land in `incidents` and `rca_reports` for a known alert
2. Inspect `telemetry_refs` and re-query backends for the same window (lineage check)
3. Capture clean baselines; compare `anomaly_baseline.n` and MAD stability
4. Inject one labeled fault; write `groundtruth` with full provenance
5. Run ranking metrics on that join (`incident_id`)
6. After ≥30 labeled examples per stratum of interest, run `corpus_pipeline` and inspect ECE / top-k on holdout
7. Sketch how `rca_reports` would land in your org’s analytics warehouse

---

## 13. Key code & docs map

| Path | For data engineers |
|---|---|
| `shared/contracts/` | Source-of-truth schemas |
| `incidents/ingest/` | Extract adapters + trigger |
| `rca/anomaly`, `rca/graphs`, `rca/scoring` | Feature + graph + score transforms |
| `deploy/helm/postgres/` | DDL / roles / migrations |
| `eval/` | Fixtures, fit, calibration, corpus gates |
| `docs/rca_kernel.md` | Scoring & graph deep dive |
| `docs/production-readiness.md` | Dataset / calibration release gates |
| `.env.example` | Lookback, scope regexes, DSNs, artifact paths |

---

## 14. FAQ (data-focused)

**Is the telemetry copied into Postgres?**  
No — references + derived RCA facts. Raw series stay in Prom/Tempo/Loki.

**Is ranking ML?**  
Primary path is deterministic scoring over engineered graph features. Calibration/weight fitting is classical stats on a labeled corpus, not a large online model.

**Where do labels come from?**  
Chaos/experiment harness or operator confirmation — never invented by the engine. Stored under `groundtruth` with provenance.

**Can I treat golden fixture metrics as production accuracy?**  
No. CI golden is an `n=1` smoke gate. Portfolio claims require the stratified labeled corpus.

**What’s the join key across the system?**  
`incident_id` ties Alertmanager-triggered incidents, RCA reports, ground truth, and remediation plans.

---

*Causa — multi-signal observability in, typed contracts through, ranked incident facts out — with labels firewalled from the live scorer.*
