# Causa — RCA Engine Specification

> **Status:** Design spec (language-agnostic)
> **Scope:** The deterministic scoring core and its evaluation protocol.
> **Data & ground truth:** **OpenTelemetry Demo** (~15 services, multi-language, pre-instrumented, shared Redis/Kafka/Postgres) on K8s, with **Chaos Mesh** injecting faults. Baselines learned from a healthy warmup window. No hand-authored telemetry.
> **Companion:** `PRD2.md` (product scope). This document resolves the scoring math that the PRD leaves abstract.

This spec is written as pseudocode + schemas. It fixes, in order, the foundational ambiguities: (1) `anomaly_score` and edge orientation, (2) the scoring math, (3) baselines/ablation, (4) the chaos scenario catalog, (5) confidence calibration, (6) scope trims.

---

## 0. Notation & data model

An **incident** is one chaos experiment run. Ground truth per incident:
```
Incident = {
  incident_id,
  true_root_node,        # the entity chaos targeted (service, DB, node, edge)
  fault_class,           # AppLogic | Config | Infra | Dependency | Resource | Data
  inject_start, inject_end
}
```

The engine sees only telemetry, never these fields, until scoring is compared against them.

Two persistent graphs (Service Dependency, Infrastructure) and two on-trigger graphs (Trace Execution, Incident Subgraph) are built as described in `KnowledgeBase/Graphs/*`. This spec adds one derived structure — the **Influence Graph** (§1.2) — on which all propagation scoring runs.

---

## 1. Item 1 — `anomaly_score` and edge orientation (the foundational primitives)

Everything downstream consumes these two. They must be defined before any signal.

### 1.1 `anomaly_score` — per-node deviation from learned baseline

**Baseline learning (warmup).** For each node and each metric `m` (e.g. `p95_latency`, `error_rate`, `cpu`, `retry_rate`, `saturation`), collect a healthy warmup window and store **robust** statistics — median and MAD (median absolute deviation), not mean/std, so a single spike or the fault itself can't poison the baseline:
```
baseline[node][m] = { median: μ_m, mad: s_m }   # s_m = 1.4826 * MAD  (≈ σ for normal data)
```

**Per-metric deviation** at window `t` (one-sided — for these metrics only increases are anomalous):
```
z_m(t)    = max(0, (x_m(t) - μ_m) / (s_m + ε))
dev_m(t)  = 1 - exp(-z_m(t) / κ)        # squash to [0,1]; κ sets sensitivity (default 3)
```

**Composite node score** — use **max across metrics**, not a weighted sum, so one sharply-broken metric isn't diluted by healthy ones (a deadlock spikes latency while CPU looks fine):
```
anomaly_score(node, t) = max over m of dev_m(t)
dev_vector(node, t)    = { m: dev_m(t) }          # retained for explainability + classification
```

**`FirstDeviationTime(node)`** = earliest `t` where `anomaly_score(node,t) > θ_fire` for `k_persist` consecutive windows (default `θ_fire=0.5`, `k_persist=3`) — persistence guards against single-window blips.

> **Known-gap flag (slow burn):** a node that is causal but never individually crosses `θ_fire` has no `FirstDeviationTime`. It is handled by subgraph inclusion via connectivity (§3) and a lowered secondary threshold, and its `T` contribution is set to neutral, not penalizing. This gap is *measured* as **subgraph recall** (§5), not hidden.

### 1.2 Edge orientation — the Influence Graph (this is the real fix for the leaf bug)

Call-graph edges point in **call direction** (`A → B` = "A calls/depends on B"), because that's what traces natively give. But *failure influence does not always flow with the call arrow*:

| Edge `call_type` | Failure influence flows | In the Influence Graph |
|---|---|---|
| sync (HTTP / gRPC / DB call) | **callee → caller** (a slow/broken B slows A) | **reverse** the edge: `B → A` |
| async / event (Kafka, queue) | **producer → consumer** (a bad producer poisons the consumer) | **keep** the edge: `A → B` |
| infra hosts service (`runs-on`) | **infra → service** | orient `infra → service` |

Construct the **Influence Graph** `IG` once per incident by re-orienting every edge by its `call_type`. All propagation signals (`D`, `P`) run on `IG`, with **infra and service nodes unified in the same graph**.

**Why this dissolves the D-at-leaves bug by construction:** a root-cause database is a *sink* in the call graph (no outgoing calls) but in `IG` its sync edges reverse, so it now has **outgoing influence edges to every service that calls it**. Its failure has somewhere to flow, so `D` (§2.2) can score it as an origin. No synthetic sink nodes, no special-case leaf prior needed — the orientation was the bug.

---

## 2. Item 2 — the scoring math

Base score is a weighted sum of **three** additive signals over `IG`. The former "Signal 4 (L)" is **not** a summand — it becomes a post-scoring re-ranking pass (§2.4), because it operates *across* nodes and a per-node linear weight cannot express "boost a different node."

```
S(N) = w_T · T(N) + w_D · D(N) + w_P · P(N)
```

All three signals ∈ [0,1]. Weights start at `1/3` each and are tuned on the training split only (§3).

### 2.1 T — temporal primacy (time-delta, not rank)

Rank throws away magnitude (deviating 4.2 s early ≠ 40 ms early), so use the **time delta**:
```
t0     = min FirstDeviationTime over subgraph nodes that have one
horizon = min(max_subgraph_delta, H)          # H caps a lone straggler from compressing the scale (default H = 60s)
T(N)   = clamp01( 1 - (FirstDeviationTime(N) - t0) / (horizon + ε) )
```
- Earliest node → `T = 1`.
- Node with no `FirstDeviationTime` (slow burn) → `T = 0.5` (neutral, flagged), so it is neither rewarded nor punished on time and must earn rank via `D`/`P`/arbitration.
- `horizon` is **data-derived** (not a free hyperparameter), capped by `H`.

### 2.2 D — cascade direction (on the Influence Graph)

```
OutInfluence(N) = Σ anomaly_score(succ)  for succ in IG.successors(N)
InInfluence(N)  = Σ anomaly_score(pred)  for pred in IG.predecessors(N)
D(N) = ( α · anomaly_score(N) + OutInfluence(N) )
       / ( α · anomaly_score(N) + OutInfluence(N) + InInfluence(N) + ε )
```
- `D → 1`: influence flows outward ⇒ **origin**.
- `D → 0`: influence flows inward ⇒ **victim**.
- Self-term `α·anomaly_score(N)` (default `α = 0.5`) guards the case where a true origin's victims are still ramping and haven't lit up yet.
- **Node role** is derived here for the output schema: `origin` (high `D`, high anomaly), `amplifier` (mid `D`, high betweenness, high incoming), `victim` (low `D`). This wires the previously-dangling betweenness/amplifier idea into an actual field.

### 2.3 P — propagation consistency (on the Influence Graph)

An origin's badness should be *demonstrably* flowing along its outgoing influence edges:
```
consistent(N→M) = (FirstDeviationTime(N) ≤ FirstDeviationTime(M))
                  AND (edge_metric_degraded(N→M))       # latency/error on that edge above its baseline
P(N) = Σ_{M in IG.successors(N)} [ consistent(N→M) · anomaly_score(M) ]
       / ( Σ_{M in IG.successors(N)} anomaly_score(M) + ε )
```
- Zero out-degree in `IG` → `P = 0`, flagged; such a node leans on `D`'s self-term, `T`, and arbitration.

### 2.4 Layer arbitration — the re-ranking pass (this is the corrected "L")

After `S(N)` is computed for all nodes, run a deterministic cross-node pass. For each service `N` and its hosting infra node `I` where `I → N` exists in `IG`:
```
if  I and N are both in the top-C candidates
and FirstDeviationTime(I) < FirstDeviationTime(N)
and anomaly_score(I) ≥ anomaly_score(N):
        promote I above N in the ranking   # infra deviated first and harder ⇒ likely origin, not the service
        annotate N.role = "victim-of-infra"
```
Explainable, deterministic, and it does the one thing a summand cannot: change the *relative order* of two different nodes. Because infra and service already share `IG`, most cross-layer cases are caught by `T`/`D` directly; this pass only arbitrates the residual near-ties.

### 2.5 Output schema
```
RootCauseCandidate = {
  node_id, node_kind,                 # service | infra | dependency
  score: S(N),
  confidence,                         # §5, calibrated
  role: origin | amplifier | victim | victim-of-infra,
  signals: { T, D, P },               # sub-scores, always exposed
  dev_vector,                         # per-metric deviations
  explanation                         # generated sentence, §2.6
}
```
Output = candidates sorted by score desc (after arbitration), plus a `fault_class` (classification, §6.4).

### 2.6 Explainability (mandatory)
Every candidate carries a generated sentence from its sub-scores, e.g.:
> *"cart-db deviated 4.2 s before its callers (T=0.96), influence flows outward to 3 services with no incoming anomaly (D=0.91), and 3/3 outgoing edges degraded in order (P=1.0). Role: origin."*

---

## 3. Item 3 — baselines, ablation, train/test

A single accuracy number is meaningless without something to beat and a held-out set.

**Comparators (must be reported alongside the headline):**
| Baseline | Ranking rule |
|---|---|
| Random | shuffle subgraph nodes |
| MostAnomalous | sort by `anomaly_score` alone |
| EarliestOnly | `T` alone |
| CascadeOnly | `D` alone |
| **Full** | `S(N)` + arbitration |

**Ablation table** — drop one signal at a time (`S` without T, without D, without P) and each single signal alone. If `EarliestOnly` ≈ `Full`, the other signals are decoration and we learn that before the demo. Nearly free once the harness exists.

**Train/test split.** Split **chaos scenarios** (injection site + fault params), not time windows, into train/test. Tune `w_T, w_D, w_P` (grid or coordinate search) on train only; report every number on the held-out test scenarios. No weight ever sees its test incident.

### 3.1 Cross-topology generalization

The scorer memorizes **nothing per-node**: T/D/P are computed from local graph structure and live deviations, so the *only* parameters that could overfit to one topology are the three global weights — a 3-dimensional surface, negligible overfit risk. Two checks make this concrete:

- **Analytical argument.** The signal definitions are topology-invariant (structural + deviation-based); transfer risk is confined to 3 scalars, not to learned node embeddings. State this explicitly.
- **In-app site split.** Hold out a disjoint set of *injection sites* within OTel Demo — services of different depth and fan-out — from weight tuning, and report top-k on the held-out sites. Because a deep chain, a wide fan-out, and a leaf origin exercise different sub-structures, this is a genuine within-app generalization test, not a restatement of the training score.
- **Stretch (deferred):** a fully separate app (Online Boutique) as a held-out topology for the strongest possible claim — same infra cost as the real-data ceiling.

---

## 4. Item 4 — chaos scenario catalog (replaces the "adversarial regimes")

Each scenario is a **Chaos Mesh** experiment on the **OpenTelemetry Demo**, with a **known injected root** = ground truth, chosen to stress a specific signal. This is the real-deployment analogue of the reviewer's adversarial regimes.

| # | Scenario | Concrete injection (OTel Demo + Chaos Mesh) | True root | Stresses | Expected weak signal |
|---|---|---|---|---|---|
| S0 | Downstream cascade (control) | flagd `productCatalogFailure` → errors propagate to frontend | product-catalog | sanity | none — should be easy |
| S1 | Slow-burn leaf | `StressChaos` ramping memory on the cart **Redis**, or CPU on a leaf | redis / leaf | `T` (origin deviates *late*) + leaf | leans on `D` self-term + arbitration |
| S2 | Retry storm / backpressure | `NetworkChaos` delay on a sync dependency → client retries saturate the caller | delayed callee | edge orientation, `D` (anomaly flows *backward*) | tests sync-reversal in `IG` |
| S3 | Clock skew | `TimeChaos` skewing one pod's clock during a fault | the faulted node | `T` / causality ordering | robustness of `FirstDeviationTime` |
| S4 | Shared-dependency confound | `NetworkChaos`/`StressChaos` on **Kafka** — checkout→accounting & fraud both degrade, no edge between them | kafka | origin has **no edge** to victims | finding the common infra ancestor |
| S5 | Infra-rooted | `StressChaos` CPU/mem on the K8s node hosting a service | the node | cross-layer | arbitration (§2.4) |

Report **per-scenario** top-k, not just the aggregate. The portfolio claim is *"X% overall, and here is exactly where and why it fails"* — S1 and S4 are the expected hard cells; naming them is the credible move.

**Volume (a dial, not a fork):** target ≥ 6 scenarios × several injection sites × repeated runs → on the order of 100+ incidents. N is bounded by wall-clock (real chaos runs), not by generation — pick the largest N the time budget allows and report it.

### 4.1 Concurrency: guard → isolation

A trigger-overlap check runs before scoring: if a second trigger fires while an analysis window is open and the anomalous subgraphs intersect or co-occur, the incident is routed to **multi-fault isolation (§7)** rather than scored as one. If isolation cannot confidently separate the faults, it **degrades to flagging** (`MULTI-INCIDENT — ambiguous`) instead of emitting a contaminated ranking. Refuse-and-flag is the safe fallback, not the default.

**Single-topology caveat (explicit):** one demo app = one graph shape. Mitigate by injecting at many distinct sites and by choosing a non-trivial app (≥10 services, mixed sync/async, a shared datastore). Generalization-across-topologies is a stated V1 limitation, not a hidden one.

---

## 5. Item 5 — confidence calibration

`S(N)`'s top value + margin over #2 is a **score**, not a probability. To earn the word "confidence," calibrate it:

```
raw_conf(incident) = S(top) · (S(top) - S(second)) / (S(top) + ε)
```
Bin `raw_conf` over all test incidents; for each bin plot predicted confidence vs **empirical top-1 correctness** → a **reliability diagram**. Fit a monotonic map (isotonic or Platt) from `raw_conf` → calibrated probability on the train split; apply on test. Report Expected Calibration Error (ECE).

**Additional gap metric — subgraph recall:** fraction of incidents where `true_root_node` was even *included* in the Incident Subgraph. Ranking accuracy is conditional on this; a slow-burn origin filtered out at subgraph construction caps top-k regardless of scoring. Report both.

---

## 6. Item 6 — scope trims

### 6.1 Remediation router — 3 branches deep, rest tabled
Build convincingly: **rollback-to-last-good**, **config-patch proposal**, **code-fix PR** (proposed, never merged). The other three (scale-up, restart/cordon, alert-only) are a **routing table** entry + stub, not full implementations. Depth over surface.

### 6.2 Human-approval surface — **CLI**
Shows ranked candidates + evidence + proposed remediation; approve → proceed, reject → next candidate. A web UI is deferred; it adds no conceptual value to the demo.

### 6.3 Amplifier / betweenness — wired, not dangling
Surfaced as `role` in the candidate schema (§2.2) and shown in explanations. Not a separate metric.

### 6.4 Failure classification — **measured (natural confounding)**
Classify the top candidate into `AppLogic | Config | Infra | Dependency | Resource | Data` from its `dev_vector`, `node_kind`, `role`, and log signatures, then route to the matching remediation branch.

**It is reported as a real metric** because on a live cluster the signatures confound *naturally* — a Chaos Mesh network fault on a dependency presents partly as a latency/resource anomaly on its callers; a Kafka stall (S4) looks like a resource issue at two unrelated consumers. We do **not** hand-author confounders (that stays a deferred stretch); we measure accuracy against the chaos-run `fault_class` ground truth and **report per-class** so the naturally-hard confusions (Dependency↔Resource, Config↔AppLogic) are visible rather than averaged away. A confusion matrix is the artifact.

**Stretch (specced, not built in V1):** designed confounder scenarios that deliberately disguise the fault class —
- **C1:** flagd pushes a bad config that makes a service spin/retry → surface signature = CPU/Resource, true class = Config.
- **C2:** an undersized connection pool (config) → presents as connection exhaustion → surface = Dependency/Resource, true = Config.
- **C3:** an app-logic bug whose only symptom is slow memory growth → surface = Resource, true = AppLogic.
These test classification *robustness* against adversarial disguise. Deferred beyond the critical path; the natural-confounding metric above stands on its own for V1.

---

## 7. Multi-fault isolation (principled)

When an incident window contains more than one **independent** fault, return **one ranked list per fault** — without (a) false-splitting a single cascade into two, or (b) false-merging two faults into one. Four stages, all on the Influence Graph `IG` restricted to the incident subgraph.

**Stage 1 — Connected components.** Partition the anomalous subgraph into `IG`-connected components. These are the first-cut candidate clusters. (Two genuinely independent faults usually already land here as separate components — this is the cheap 80%.)

**Stage 2 — Gap repair (fixes false splits).** A single cascade can appear as two components when an intermediate relay didn't cross `θ_fire`. Merge two components when they are linked by a short `IG` path whose intermediate nodes are **quiet relays** — below `θ_fire` but above the secondary `θ_low` (§1.1) — *and* the path shows monotonic temporal ordering. One quiet node in the middle no longer masquerades as two incidents.

**Stage 3 — Origin clustering (fixes false merges).** Within a component, find **temporal-source origins**: nodes that deviated before all their `IG`-predecessors (local `FirstDeviationTime` minima) and carry high `D`. Two such origins are **distinct faults** iff **both**:
- their onset times differ by more than a coupling window `Δ` (they did not co-start), **and**
- neither lies on a temporally-consistent influence path to the other (no causal link between them).

Otherwise it is one cascade (earlier origin = root, later = amplifier). Split a multi-origin component by **causal attribution**: assign every node to the earliest temporal-source origin that reaches it via consistent edges — a causal Voronoi partition.

**Stage 4 — Independent scoring.** Run §2 (+ arbitration) per cluster → *k* ranked lists, each with its own calibrated confidence.

**Failure modes (stated, not hidden):**
- **`Δ` sensitivity** — two independent faults starting within `Δ` merge. Reported.
- **Single-connector over-merge** — two faults joined only through a shared victim can over-merge; mitigated by requiring a *single-source* consistent path in Stage 2.
- **Causally-adjacent simultaneous faults** are inherently ambiguous → isolation **abstains** (degrade-to-flag, §4.1) rather than guess.

**Evaluation — dual-injection catalog** (two simultaneous Chaos Mesh faults):

| # | Setup | Tests |
|---|---|---|
| D1 | Faults at two disjoint services (no shared deps) | clean split (Stage 1) |
| D2 | Two services sharing a downstream dep, both faulted | false-merge handling (Stage 3) |
| D3 | Two faults with onsets within `Δ` | expected-hard / abstention |

**Metrics:** cluster-count accuracy (did it find the right *number* of faults?), per-fault top-k (does each true root top exactly one returned cluster?), and partition quality (**Adjusted Rand Index** of node→fault assignment vs ground truth).

---

## 8. Resolved-decisions summary

| Ambiguity | Resolution |
|---|---|
| `anomaly_score` undefined | Robust one-sided z-score vs learned MAD baseline, squashed, **max** across metrics; vector retained |
| Baseline source | Learned from healthy warmup window (no generator truth) |
| Edge orientation | **Influence Graph**: reverse sync edges, keep async, unify infra+service. Fixes D-at-leaves structurally |
| Signal 4 (L) miscast as summand | Recast as **layer-arbitration re-ranking pass** (§2.4); base score is 3 additive signals |
| D degenerate at leaves | Solved by `IG` reversal + self-anomaly term |
| T normalization | Time-delta with data-derived horizon capped at `H`; neutral for no-deviation nodes |
| No baseline/ablation | Random / MostAnomalous / per-signal comparators + ablation table, held-out split |
| "Confidence" ≠ probability | Reliability diagram + isotonic calibration + ECE; plus subgraph-recall gap metric |
| Adversarial coverage | Chaos scenario catalog S0–S5 on OTel Demo + Chaos Mesh, reported per-scenario |
| Amplifier/betweenness dangling | Wired into `role` field |
| Router over-broad | 3 branches deep + routing table |
| Testbed | OpenTelemetry Demo (~15 services, pre-instrumented) |
| Chaos tool | Chaos Mesh (declarative experiments = clean ground truth) |
| Classification | Measured metric via natural confounding + per-class confusion matrix (§6.4) |
| Concurrent incidents | **Principled multi-fault isolation built** (§7); abstains when ambiguous |
| Cross-topology | Analytical argument + in-app site split (§3.1) |

## 9. Remaining dials (not design forks)
- **N** — number of chaos runs, bounded by wall-clock; report whatever the time budget yields (target 100+).
- **Isolation `Δ` / abstention threshold** — the coupling window and the confidence below which isolation flags instead of splits; tuned on dual-injection scenarios.
- **Hand-authored classifier confounders (C1–C3)** — specced in §6.4, deferred beyond critical path.
- **Second held-out app (Online Boutique)** — deferred stretch for cross-topology (§3.1).
- **Multi-fault at scale (3+ simultaneous)** — catalog covers pairs; higher-order left as future work.
