# Causa — Technical Brief for Cloud Engineers

**Audience:** Cloud / platform / SRE engineers evaluating Causa for a controlled trial or architecture review  
**Companion brief:** [`DATA_ENGINEER_BRIEF.md`](DATA_ENGINEER_BRIEF.md) (contracts, schemas, corpus, calibration)  
**Status:** Development and controlled evaluation (not production-auto-remediation ready)  
**Repo layout:** Domain packages under `incidents/`, `rca/`, `remediation/`, `observability/`, `dashboard/`, `shared/`

---

## 1. One-sentence pitch

Causa is a **Kubernetes-native, observability-driven root-cause analysis system** that turns Prometheus alerts + Tempo traces + Loki logs into a ranked, explainable influence graph — and optionally prepares **operator-approved** remediation plans (never silent auto-apply by default).

---

## 2. Problem it addresses

When a microservice degrades, on-call engineers typically:

1. Get a flood of correlated alerts (cascades, not roots)
2. Jump between Grafana/Prometheus/Tempo/Loki manually
3. Reconstruct call graphs and timing by hand
4. Guess whether the fault is app logic, config, dependency, or infra

Causa automates steps 1–3 into a deterministic pipeline:

> **Alert → scoped incident window → anomaly scoring → influence graph → ranked root-cause candidates → proposed remediation (human gate)**

It is designed for **Kubernetes microservice topologies** instrumented with OpenTelemetry (validated against the OpenTelemetry Demo / `otel-demo` namespace).

---

## 3. Value for a cloud / platform team

| Concern | What Causa brings |
|---|---|
| MTTR | Cuts “which service is the origin?” from graph+time+anomaly signals |
| Alert noise | Treats the firing service as a *victim candidate*, not automatically the root |
| Explainability | Every ranked node gets T/D/P signal scores and a human-readable explanation |
| Blast-radius control | Remediation is proposal-only until calibration gates pass; kill switches + allowlists + lease-based workers |
| Ops fit | Helm charts, OTel gateway pattern, Postgres persistence, Alertmanager webhook — standard cloud-native pieces |
| Safety | Separate DB roles, fail-closed auth tokens, no public ingress assumed |

---

## 4. High-level architecture

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                        TELEMETRY PLANE (in-cluster)                       │
│  App pods ──► OTel Agent (DaemonSet)                                      │
│                 ├─ traces ──► OTel LB (trace-ID affinity) ──► Collector    │
│                 └─ metrics/logs ───────────────────────────► Collector    │
│                        │                                                  │
│                        ▼                                                  │
│              Tempo ◄── MinIO    Loki ◄── MinIO    Prometheus ──► Mimir    │
│                                           │                               │
│                                           ▼                               │
│                                      Alertmanager                         │
└───────────────────────────────────┬──────────────────────────────────────┘
                                    │ webhook (+ shared auth token)
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         CAUSA CONTROL PLANE                               │
│                                                                           │
│  causa-ingest          stage2-rca              Postgres                   │
│  (scope window,        (anomaly → graphs →     (incidents, RCA reports,   │
│   snapshot, outbox)     T/D/P rank → store)     baselines, remediation)   │
│                                                                           │
│  dashboard API/UI      remediation worker                                 │
│  (incidents, graphs,   (lease plans → apply                               │
│   propose / approve)    only after approval)                              │
└──────────────────────────────────────────────────────────────────────────┘
```

**Pipeline (runtime):**

```text
Alertmanager
  → causa-ingest   (open incident, pull telemetry snapshot, durable outbox)
  → stage2-rca     (anomaly detection, influence graph, isolation, ranking)
  → Postgres
  → dashboard      (operators review ranked causes)
  → proposed plan  → human approver → leased remediation worker
```

---

## 5. Component inventory

### 5.1 Observability stack (Helm under `observability/helm/`)

| Component | Role |
|---|---|
| **OTel Agent** | Per-node DaemonSet; collects and forwards telemetry |
| **OTel LB** | Trace-ID load balancer so tail-sampled traces land on one gateway pod |
| **OTel Collector** | Gateway: processing, tail sampling, export to backends |
| **Prometheus** | Scrape + alerting rules; remote_writes to Mimir |
| **Mimir** | Long-term metrics storage (MinIO-backed) |
| **Tempo** | Distributed traces (MinIO-backed) |
| **Loki** | Logs (MinIO-backed) |
| **MinIO ×3** | Object storage for Tempo / Loki / Mimir blocks |
| **kube-state-metrics** | Kubernetes object metrics |
| **node-exporter** | Node CPU/memory for infra-rooted RCA |
| **Alertmanager** | Routes firing alerts to Causa ingest webhook |

### 5.2 Causa application services

| Service | Chart / path | Responsibility |
|---|---|---|
| **causa-ingest** | `incidents/helm/causa-ingest` | Alertmanager webhook; scopes monitored services; builds incident window; persists state; forwards to Stage-2 via outbox |
| **stage2-rca** | `rca/helm/stage2-rca` | Anomaly, four graphs → influence graph, T/D/P scoring, layer arbitration, write `rca_reports` |
| **Postgres** | `deploy/helm/postgres` | Incidents, baselines, RCA reports, outbox, remediation plans/events |
| **Dashboard API + React UI** | `dashboard/` | Incident browser, graphs, telemetry panes, remediation propose/approve |
| **Remediation worker** | `remediation/helm/causa-remediation-worker` | Claims approved plans with DB leases (`SKIP LOCKED`), executes allowlisted actions, observes SLO / canary |
| **Gatekeeper policies** | `remediation/helm/gatekeeper-remediation` | Optional OPA/Gatekeeper constraints for remediator mutations |

### 5.3 Supporting / evaluation

| Area | Purpose |
|---|---|
| `eval/` | Offline fixtures, baselines, weight fitting, calibration, corpus pipeline |
| `shared/contracts/` | Stdlib-only C1–C5 type seams between ingest / RCA / remediation |
| `shared/profiles/` | App profiles (e.g. `otel-demo`) for service allowlists and action capabilities |
| Chaos / inject scripts | Fault injection against OTel Demo for corpus building |

---

## 6. RCA engine (what happens after an alert)

Deterministic path — not an opaque LLM black box for ranking:

```text
IncidentTelemetry + AnomalyOutput[]
  → topology + traces + anomalies
  → four graphs:
        Infrastructure (persistent)
        Service Dependency (persistent)
        Trace Execution (on-trigger)
        Incident Subgraph (on-trigger)
  → Influence Graph (IG)
        sync edges reversed (callee → caller influence)
        async edges kept (producer → consumer)
        runs-on oriented infra → service
  → score S(N) = w_T·T + w_D·D + w_P·P
  → infra vs service layer arbitration
  → RankedResult (candidates + explanations + confidence margin)
```

| Signal | Meaning |
|---|---|
| **T (Temporal)** | Who moved first relative to first deviation in the subgraph |
| **D (Directional)** | Out/in influence weighted by successor/predecessor anomaly |
| **P (Propagation)** | Consistency of temporal order along degraded edges |

**Confidence today** is a **rank margin**, not a calibrated probability, until a fitted calibration artifact is loaded. Automatic remediation refuses to run without a validated calibration map (≥30 labeled incidents per remediable action class).

Fault classes (for remediation routing): AppLogic, Config, Dependency, Resource, Infra, Data, Timing (taxonomy + stubs vary by evidence).

---

## 7. Remediation model (cloud-safety critical)

Causa is intentionally **proposal-first**:

| Mode | Default | Notes |
|---|---|---|
| Auto-propose | **OFF** (`CAUSA_REMEDIATION_AUTO_PROPOSE=0`) | |
| Auto-apply | **OFF** (`CAUSA_REMEDIATION_AUTO_APPLY=0`) | Hard-gated by calibration |
| Kill switch | Available global / per-NS / per-service | |
| Infra actions (scale/restart/cordon) | Blocked unless `CAUSA_REMEDIATION_ALLOW_INFRA=1` | |
| Code-fix PRs | Draft only; never auto-merged | Optional GitHub + NVIDIA NIM narrative |

**Router (fault class → action family):**

| Fault class | Proposed action |
|---|---|
| AppLogic | Draft code-fix PR |
| Config | ConfigMap / config patch plan |
| Dependency | Rollback-to-last-good style plan |
| Others | Stub / alert-only until capability exists |

**Execution controls a cloud engineer should care about:**

- Namespace allowlist (`REMEDIATION_ALLOWED_NAMESPACES`)
- Service allowlist from runtime profile
- Confidence floor + risk tier
- Plan expiry + idempotency keys
- Worker leases with heartbeat / expiry takeover (`scripts/drill-remediation-leases.sh`)
- Live Prometheus SLO snapshots before/after canary observation
- Dedicated K8s service account + dedicated Postgres role `causa_remediator`

---

## 8. Data & persistence

**Postgres** is the system of record. Important tables:

| Table | Content |
|---|---|
| `incidents` | Alert-opened incident windows + snapshot status |
| `rca_reports` | Ranked RCA output, root cause, failure type, confidence |
| `anomaly_baseline` | Per-service baseline stats for anomaly detection |
| `stage2_outbox` | Durable forward from ingest → Stage-2 |
| `remediation_plans` / `remediation_events` | Plans, leases, audit trail |
| `groundtruth.ground_truth` | Labeled eval corpus |

**Least-privilege DB roles:**

| Role | Intent |
|---|---|
| `rca_engine` | Write RCA / incident path |
| `causa_remediator` | Own remediation plans/events only |
| `causa_eval` | Read-only evaluation |

Object storage for telemetry backends is **MinIO in the reference deploy**; in a real cloud cluster these map cleanly to S3 / GCS / Azure Blob via the same Tempo/Loki/Mimir configs.

---

## 9. Deployment model

### Reference local path (Kind)

```bash
cp .env.example .env   # fill secrets / tokens
./causa-startup.sh     # Kind cluster + stack + port-forwards + API :8090 + UI :5174
```

Full stack install (cluster must already be reachable):

```bash
./deploy/scripts/deploy.sh dev                 # or: prod
./deploy/scripts/deploy.sh dev --stage2 mock   # mock Stage-2 for plumbing tests only
```

`deploy.sh` installs in dependency order: exporters → MinIOs → Tempo/Loki/Mimir → Prometheus → OTel gateway/LB/agent → Alertmanager → Postgres → Stage-2 → causa-ingest.

Prod path accepts `STORAGE_CLASS=<class>` for PVCs.

### Namespaces (typical)

Telemetry and Causa components are split across namespaces (`prometheus`, `loki`, `tempo`, `otel-agent`, `postgres`, `causa`, app NS such as `otel-demo`, etc. — see `deploy/manifests/namespaces.yaml`).

### Images / runtime

- Python **≥3.11** application services (FastAPI-style triggers, workers)
- React product dashboard (`dashboard/frontend`)
- Container images via Dockerfiles next to each domain (`incidents/`, `rca/`, `remediation/`, `dashboard/`)
- Production guidance: **immutable tags or digests only**

### Local verification ports (after startup)

| Service | URL |
|---|---|
| Product UI | http://127.0.0.1:5174 |
| Dashboard API | http://127.0.0.1:8090 |
| Prometheus | http://127.0.0.1:9090 |
| Tempo | http://127.0.0.1:3200 |
| Loki | http://127.0.0.1:3100 |
| Postgres | 127.0.0.1:15432 |

---

## 10. Integration requirements (what you need in *your* cluster)

To trial Causa against a real workload:

1. **Kubernetes** with Helm + storage class for PVCs (or managed equivalents for Tempo/Loki/Mimir/Postgres)
2. **OTLP instrumentation** on target services (or existing scrape paths into this Prometheus/Tempo/Loki)
3. **Alert rules** that fire on service health (error rate, latency, resource pressure)
4. **Alertmanager** webhook pointing at causa-ingest with shared `TRIGGER_AUTH_TOKEN`
5. Env scope:
   - `CAUSA_APP_NAMESPACE`
   - optional `CAUSA_MONITORED_SERVICES_REGEX` / `CAUSA_EXCLUDED_SERVICES_REGEX` (critical to filter infra noise)
6. Clean **baselines** captured with chaos disabled before trusting anomaly scores

Causa does **not** require replacing an existing Grafana stack; it sits beside Prometheus/Tempo/Loki and consumes them.

---

## 11. Security posture (honest)

| Topic | Current stance |
|---|---|
| Network | Designed for **trusted private networks**; do not put dashboard / webhooks on public ingress without an authenticating proxy or mesh policy |
| Trigger auth | Fail-closed without `TRIGGER_AUTH_TOKEN` (local override only via `CAUSA_TRIGGER_ALLOW_UNAUTHENTICATED=1`) |
| API auth | Viewer / proposer / approver tokens (`CAUSA_API_*`, `REMEDIATION_APPROVAL_TOKEN`) |
| Secrets | NVIDIA NIM, GitHub, DB passwords via env / K8s secrets — never committed |
| Remediation | Separate role + SA; kill switches; calibration gate blocks auto-apply |
| File store | JSON remediation store only for single-process local demos (`CAUSA_REMEDIATION_ALLOW_FILE_STORE=1`) |

See `SECURITY.md` and `docs/production-readiness.md` for the full gate list.

---

## 12. Maturity & readiness (what to tell leadership)

**Suitable today for:** Kind / staging demos, architecture review, controlled evaluation against a labeled chaos corpus, operator-in-the-loop RCA assist.

**Not ready for:** Unsupervised auto-remediation in production.

Production-readiness gates (summary):

- Versioned labeled corpus covering fault classes, alert families, topologies
- Disjoint train / calibration / holdout sets
- ≥30 calibration incidents per auto-remediable action; holdout metrics published (top-1/top-3/MRR, abstention, ECE, TTD)
- Network restriction for APIs/webhooks
- Immutable images; backup/restore + lease-expiry drills; canary rollback failure surfaced correctly
- Fitted weights + calibration artifact versioned with the release

The runtime **rejects automatic execution** when a non-constant calibration map with sufficient `n` is absent.

---

## 13. Repository map (for reviewers)

```text
causa/
├── incidents/          # Stage-1 ingest + webhook trigger
├── rca/                # Anomaly, graphs, scoring, isolation, Stage-2 service
├── remediation/        # Router, policy, worker, SLO gates, NIM/GitHub branches
├── observability/helm/ # Full telemetry plane charts
├── alerts/helm/        # Alertmanager
├── deploy/             # Postgres chart, namespaces, deploy.sh
├── dashboard/          # Product API + React UI (+ optional light telemetry UIs)
├── shared/contracts/   # Cross-component type seam (stdlib only)
├── shared/profiles/    # App capability profiles
├── eval/               # Fixtures, baselines, fit/calibration harness
├── docs/               # Design notes + this brief
├── causa-startup.sh    # One-shot local Kind bring-up
└── .env.example        # Full configuration reference
```

---

## 14. Suggested evaluation plan for a cloud engineer

1. **Deploy** Kind or staging cluster with `./deploy/scripts/deploy.sh dev`
2. **Instrument / attach** a known microservice app (OTel Demo is the reference)
3. **Capture clean baselines** (all chaos flags off)
4. **Inject a single known fault**; confirm Alertmanager → ingest → RCA report in Postgres / UI
5. **Review ranked candidates** for explainability vs your mental model
6. **Propose (do not auto-apply)** a remediation plan; walk approval + lease worker path
7. **Assess** storage sizing (MinIO/Tempo/Loki/Mimir), auth, RBAC, and how charts map to your managed cloud services (RDS/Cloud SQL, S3, GKE/EKS/AKS)

Success criteria for a pitch demo: a known injected fault produces a non-abstained RCA that names the true origin (or correctly abstains when ambiguous), with a clear explanation an SRE can defend.

---

## 15. Talking points / FAQs

**Is this another AIOps LLM wrapper?**  
Ranking is deterministic graph+signal math. LLMs (NVIDIA NIM) are optional for remediation narrative / PR text, not for inventing the root cause.

**Does it replace Prometheus/Grafana?**  
No. It consumes Prometheus, Tempo, and Loki and adds incident-scoped RCA + remediation workflow.

**Will it restart my pods automatically?**  
Not with defaults. Auto-apply is off and calibration-gated; infra actions are separately locked.

**What if confidence looks low?**  
Expected until baselines are clean, weights are fitted (`eval` corpus pipeline), and a calibration map exists. Raw confidence is a margin between top candidates.

**Can it run on EKS/GKE/AKS?**  
Yes in principle: replace Kind+MinIO+in-cluster Postgres with managed equivalents; keep Helm services and webhook wiring. Charts already distinguish `dev` vs `prod` values and storage class.

---

## 16. Key documents in-repo

| Doc | Use |
|---|---|
| `README.md` | Project overview + safety status |
| `SECURITY.md` | Trust boundaries and required config |
| `docs/production-readiness.md` | Hard gates before auto-remediation |
| `docs/rca_kernel.md` | Deep dive on influence graph + scoring |
| `incidents/ingest/README.md` | Telemetry adapter / trigger details |
| `.env.example` | Exhaustive ops/config knobs |

---

*Causa — observability in, ranked cause out, humans still hold the keys.*
