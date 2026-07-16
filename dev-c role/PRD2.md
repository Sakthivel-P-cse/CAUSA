# Causa — Product Requirements Document

> **Status:** Final draft for build
> **Date:** 2026-07-15
> **Positioning:** Portfolio / learning project
> **The one-liner:** *Causa ingests microservice telemetry, reconstructs the incident as a set of dependency graphs, and uses a deterministic, explainable scoring engine to rank the most probable root cause — then routes a human-gated remediation (rollback, config patch, scale, or code-fix PR) through a canary deploy.*

---

## 1. Overview

Causa detects failures in a network of microservices and identifies **why** they happened. When telemetry trips an anomaly trigger, Causa reconstructs the incident as dependency graphs, runs a deterministic scoring engine to produce a **ranked list of probable root-cause nodes** — each with a confidence score and a human-readable explanation — and then routes an appropriate remediation for a human to approve before it is applied through a progressive canary deploy.

The center of gravity is the **RCA engine**. Everything else exists to make the RCA engine demonstrable and its output actionable.

---

## 2. Goals & Non-Goals

### Goals
1. **Deterministic, explainable RCA.** Given an incident, output a ranked list of probable root-cause nodes, each with a confidence score and a plain-language "why."
2. **Provable accuracy.** A synthetic telemetry generator with known injected faults plus an evaluation harness that reports **top-k accuracy** over N incidents.
3. **A believable end-to-end loop** for the demo: detect → RCA → remediation proposal → human approval → canary → done.
4. **Remediation as a router**, not just code generation: the failure taxonomy decides whether the fix is a rollback, config patch, scale action, restart, alert-only, or a code-fix PR.

### Non-Goals
- Production-grade multi-tenancy, RBAC, and PII redaction (documented as future; not built in this scope).
- LLM code fixes validated against a live repo test suite (the code-fix path produces a *proposed PR*; its correctness is not guaranteed and is out of scope for the success metric).
- Building a bespoke progressive-delivery controller.
- Broad programming-language coverage. Language is irrelevant to this scope.
- Learned/ML causal inference (e.g. Granger, PC algorithm). Deferred — the RCA engine is deterministic scoring only.

---

## 3. Success Metrics

**Primary (headline claim):**
- **Top-k root-cause accuracy** on chaos-injected faults: *"the correct root cause appears in the top-3 ranked candidates X% of the time across N incidents on a live cluster, spanning M fault scenarios."*
- Reported **per chaos scenario** (see `RCA_Engine_Spec.md` §4) so the hard cases are visible, not averaged away.

**Secondary:**
- Mean rank / mean reciprocal rank of the true root cause.
- **Subgraph recall** — was the true root cause even included for scoring? (Caps top-k independent of ranking.)
- **Failure-classification accuracy** — per-class confusion matrix over chaos `fault_class` ground truth (natural confounding; `RCA_Engine_Spec.md` §6.4).
- **Multi-fault isolation** — cluster-count accuracy, per-fault top-k, and partition quality (Adjusted Rand Index) on dual-injection scenarios (`RCA_Engine_Spec.md` §7).
- **Cross-topology generalization** — top-k on held-out injection sites within the app (`RCA_Engine_Spec.md` §3.1).
- Confidence calibration (reliability diagram + ECE).
- Baselines + ablation vs the full scorer (Random / MostAnomalous / per-signal).
- End-to-end demo completes without manual intervention beyond the intended human-approval gate.

> The full scoring math and evaluation protocol live in **`RCA_Engine_Spec.md`**.

---

## 4. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  TELEMETRY SOURCE                                           │
│  • Real microservices deployment on K8s (≥10 services,     │
│    mixed sync/async, shared datastore)                     │
│  • Chaos injection = KNOWN root cause per run = ground     │
│    truth. Telemetry is emergent, not hand-authored.        │
│  • Flows through the existing OTel stack (Tempo/Loki/Prom) │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  1. INGESTION                                              │
│  • Typed telemetry envelope (signal-specific)             │
│  • spans / metric points / log records kept distinct      │
│  • Causality-safe ordering (trace DAG > logical clock >   │
│    wall-clock fallback)                                    │
│  • Storage: incident/RCA state in Postgres; raw kept as   │
│    references                                             │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  RCA TRIGGER  (Prometheus-rule / threshold anomaly)       │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  2. RCA ENGINE  ★ THE BRAIN ★                              │
│                                                           │
│  a) Graph Construction                                    │
│     Persistent:  Infrastructure Graph, Service Dep Graph  │
│     On-trigger:  Trace Execution Graph, Incident Subgraph │
│                                                           │
│  b) Deterministic Inference (scoring)                     │
│     • FirstDeviationTime ordering                         │
│     • Incoming vs Outgoing anomaly pressure               │
│     • Propagation consistency along edges                 │
│     • Cross-layer (infra-before-service) check            │
│     → OriginProbability per node                          │
│                                                           │
│  c) Failure Classification (taxonomy)                     │
│     → routes to the correct remediation type              │
│                                                           │
│  Output: ranked root-cause candidates + confidence +      │
│          failure class + evidence trail                   │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  3. REMEDIATION ROUTER                                     │
│  App Logic Fault  → LLM code-fix PR (proposed, not merged)│
│  Config Fault     → config patch proposal                 │
│  Infra Fault      → scale / restart / node action         │
│  Dependency Fault → circuit-break / rollback              │
│  Resource Fault   → scale-up proposal                     │
│  Data Fault       → alert-only                            │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  4. HUMAN-GATED APPROVAL                                   │
│  Shows: root cause, evidence, proposed remediation, diff  │
│  Approve → proceed;  Reject → next candidate              │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  5. APPLY + CANARY  (thin wrapper / simulated)           │
│  Progressive rollout, watch SLOs, auto-rollback on breach │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. The RCA Engine (detailed — this is where effort goes)

### 5.1 Graph Construction

Four graphs:

| Graph | Lifetime | Node = | Purpose |
|---|---|---|---|
| Infrastructure | Persistent | K8s node / pod / DB / cache / LB / external API | Rule out platform failure first |
| Service Dependency | Persistent | One logical service | Blast radius, propagation, origin |
| Trace Execution | On-trigger | One span (DAG) | First-failure + critical path within a request |
| Incident Subgraph | On-trigger | Anomalous nodes only | Shrink 100s of services → the few that matter |

Node and edge attributes (deviation scores, baselines, propagation indicators) are specified in `KnowledgeBase/Graphs/*`.

### 5.2 Deterministic Inference — the scoring algorithm

For each candidate node **N** in the Incident Subgraph, compute an **OriginProbability** as a weighted combination of four signals. All signals are normalized to [0,1].

**Signal 1 — Temporal primacy (T).**
Root causes deviate earliest. Rank nodes by `FirstDeviationTime`; earliest → highest T. (The origin deviates before its victims.)

**Signal 2 — Cascade direction (D).**
```
IncomingAnomalyWeight = Σ anomaly_score(upstream neighbors)
OutgoingAnomalyWeight = Σ anomaly_score(downstream neighbors)
D = OutgoingAnomalyWeight / (IncomingAnomalyWeight + OutgoingAnomalyWeight + ε)
```
- D → 1: outgoing ≫ incoming ⇒ **origin**
- D → 0: incoming ≫ outgoing ⇒ **victim**
- both high ⇒ **amplifier** (flag separately via betweenness)

**Signal 3 — Propagation consistency (P).**
Fraction of N's outgoing edges where `N deviated before neighbor` AND `edge latency/error increased`. High P = N's badness demonstrably flows outward.

> `D` and `P` are computed on the **Influence Graph** — call edges re-oriented in the direction failure actually propagates (sync edges reversed, async kept, infra unified with services). This is what lets a root-cause leaf (a DB, cache, external API) score as an origin instead of a permanent victim. See `RCA_Engine_Spec.md` §1.2.

**Final score (three additive signals):**
```
S(N) = w_T·T + w_D·D + w_P·P          # weights start at 1/3, tuned on a held-out train split
```

**Layer arbitration (post-scoring re-rank, not a 4th signal).**
Cross-layer precedence can't be an additive weight — a per-node term can't boost a *different* node. Instead, after scoring, a deterministic pass promotes an infra node above the service it hosts when the infra deviated earlier and harder. See `RCA_Engine_Spec.md` §2.4.

Output is the sorted list (post-arbitration); confidence is **calibrated** against empirical top-1 correctness, not raw score (§3 secondary metrics, `RCA_Engine_Spec.md` §5).

**Explainability is mandatory:** every candidate carries its four sub-scores and a generated sentence (e.g. "payment-service deviated 4.2s before its downstream neighbors, with 0 incoming anomaly and 3 degraded outgoing edges").

### 5.3 Failure Classification

Before remediation, classify the top candidate into one of six classes (App Logic / Config / Infra / Dependency / Resource / Data) using its signals (log signatures, resource saturation, config-change deltas, dependency health). Only **App Logic Faults** proceed to code extraction; the rest route elsewhere. This gate is what prevents needless code churn.

---

## 6. Testbed + Eval Harness (real deployment + chaos)

**Why real, not synthetic:** with a hand-written generator, *we* author the propagation, so the scorer is graded on a world built to its own assumptions. With chaos on a real app, telemetry is **emergent** (real retries, clock skew, backpressure, partial failures) while the chaos experiment still gives clean ground truth. This defuses the central circularity objection.

- **Testbed:** the **OpenTelemetry Demo** (~15 services, multi-language, pre-instrumented, shared Redis/Kafka/Postgres) on K8s, emitting through the existing OTel stack. Baselines are **learned from a healthy warmup window** — the scorer never sees any injected truth.
- **Chaos tool:** **Chaos Mesh** — declarative experiments (latency, stress, network, time-skew, pod-kill) give clean per-run ground truth.
- **Ground truth:** each chaos run records `(incident_id, true_root_node, fault_class, inject_window)`.
- **Chaos scenario catalog (S0–S5):** each scenario stresses a specific signal — slow-burn leaf, retry storm/backpressure, clock skew, shared-dependency confound (Kafka), infra-rooted. Full table in `RCA_Engine_Spec.md` §4.
- **Concurrency:** **principled multi-fault isolation** partitions overlapping incidents into one ranked list per fault (connected-components → gap-repair → causal origin-clustering), and **abstains** when faults are inseparable rather than guessing (`RCA_Engine_Spec.md` §7). Validated with dual-injection chaos scenarios (D1–D3).

**Eval harness:** run N incidents across the catalog → feed each through the RCA engine → compare ranked output to ground truth → emit `top-1/3/5`, MRR, subgraph recall, and calibration — **per scenario** and aggregate, always against Random / MostAnomalous / per-signal baselines. Drives weight tuning on a held-out train/test split over scenarios.

**Trade-offs (accepted, stated):** smaller N than synthetic (chaos runs cost real minutes) and a single topology. Mitigated by many injection sites; generalization-across-topologies is a stated V1 limitation.

---

## 7. Remediation Router

Code fix is one branch among several — the failure class decides:

| Failure class | Remediation | Human sees |
|---|---|---|
| App Logic | LLM opens a **PR** with a proposed diff + the evidence trail | Diff + rationale |
| Config | Proposed config patch (env / flag / manifest) | Before / after |
| Infra | Scale / restart / cordon action | Action + target |
| Dependency | Circuit-break or **rollback to last-good** | What rolls back |
| Resource | Scale-up proposal | New limits |
| Data | **Alert-only** (no auto action) | Alert payload |

The LLM code-fix branch uses repo + failing-context, produces a PR, and is **never auto-merged**.

---

## 8. Modules: Build Status

| Module | Status | Notes |
|---|---|---|
| Ingestion | **Build (thin)** | Typed envelope reading from the OTel stack (Tempo/Loki/Prom) fed by the real app |
| RCA Engine | **Build (deep)** ★ | The focus. Graphs + deterministic scoring + taxonomy |
| Remediation Router | **Build (medium)** | Routing logic real; code-fix branch is best-effort |
| Human Approval | **Build (thin)** | Simple UI/CLI showing evidence + approve/reject |
| Apply + Canary | **Simulate / wrap** | Argo Rollouts or a simulated progressive rollout |
| Stage-1 OTel stack | **Available** | Kept as the "real source" future path; not required for the demo |

---

## 9. Data Model (storage)

Multi-tier, with a relational DB only for state:
- Raw telemetry → object storage / kept as references (or in-memory for the synthetic path).
- Metrics → TSDB · Traces → trace store · Logs → log index (all *future*; the synthetic path can hold these in memory/files).
- **PostgreSQL** → incidents, RCA candidates + scores, remediation state, human decisions, eval results. This is the only DB required for the build.

---

## 10. Build Milestones

1. **M1 — Testbed + telemetry + graphs.** Deploy the real app on K8s through the OTel stack, learn warmup baselines, build the graphs + Influence Graph, wire one chaos scenario end-to-end with ground-truth logging.
2. **M2 — Deterministic scorer.** Implement `anomaly_score`, the Influence Graph, T/D/P signals + layer arbitration; explainability strings. (Per `RCA_Engine_Spec.md`.)
3. **M3 — Eval harness + tuning.** Chaos catalog S0–S5, top-k/MRR/subgraph-recall + baselines + ablation, held-out split, weight tuning, calibration, and the in-app site split for generalization. *← headline metric lands here.*
   - **M3.5 — Multi-fault isolation.** Build the four-stage isolation (§7); validate on dual-injection scenarios D1–D3. Depends on M2's scorer + Influence Graph.
4. **M4 — Remediation router.** 3 branches deep (rollback / config-patch / code-fix PR) + routing table for the rest.
5. **M5 — Human approval (CLI) + simulated canary.** Close the loop for the demo.
6. **M6 — Polish + writeup.** Architecture diagram, per-scenario accuracy + reliability diagram, demo recording.

**Critical path is M1→M3.** If only those land, the project still has a defensible, provable core.

---

## 11. Decisions Log (all forks resolved)

Baselines → learned warmup. Edge orientation → Influence Graph. "L" signal → arbitration pass. Canary → simulated. Approval surface → CLI. Code-fix branch → proposed PR (mock for demo). Testbed → OpenTelemetry Demo. Chaos → Chaos Mesh. Classification → measured via natural confounding + confusion matrix. **Concurrency → principled multi-fault isolation built (abstains when ambiguous). Cross-topology → analytical argument + in-app site split.**

Remaining are **dials, not forks** (`RCA_Engine_Spec.md` §9): total N of chaos runs (time-bounded), isolation `Δ`/abstention threshold, hand-authored classifier confounders C1–C3 (specced, deferred), a second held-out app (cross-topology stretch), and 3+-simultaneous-fault isolation (pairs covered, higher-order future).

---

## 12. Design Notes (Stage-1 stack rationale)

Two recurring questions about the telemetry layer, resolved:

**Why does the OTel Collector push to in-cluster backends — is that optimal?** The collector-as-gateway pattern is correct: apps know one endpoint, the collector fans out over ClusterIP DNS (low-latency, normal). Two caveats hold for scale: (1) a single collector is a SPOF/bottleneck — production uses a per-node **agent DaemonSet → gateway Deployment pool**; (2) the **metrics path is the weak link** — pushing to Prometheus via `remote_write` doesn't scale writes; at volume, target a write-first store (**Mimir / Thanos / VictoriaMetrics**), not vanilla Prometheus.

**Why traces over gRPC but metrics/logs over HTTP?** The asymmetry is on the **export** side and is dictated by each backend's ingestion API, not a choice: Tempo speaks OTLP → gRPC; Prometheus `remote_write` is HTTP-only (no gRPC variant); Loki's push API is HTTP (its `:9096` gRPC port is internal). On ingestion (app → collector) all three signals use gRPC `:4317`, with `:4318` HTTP as the fallback. Rule: **gRPC where the backend speaks OTLP, HTTP where it only offers an HTTP write API.**

---

*Companion spec: `RCA_Engine_Spec.md` (scoring math + eval protocol).*
*Incorporates: `KnowledgeBase/Graphs/*`.*
