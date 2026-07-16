# Dev A — Inputs: Testbed, Telemetry, Anomaly

> **Lane:** everything upstream of the graph. You produce the world the RCA engine reasons about, and the ground truth it's graded against.
> **Companions:** `Team_Split.md` (contracts + git), `PRD2.md` §4/§6/§8, `RCA_Engine_Spec.md` §0/§1.1/§4/§7.
> **You own directories:** `testbed/`, `ingest/`, `anomaly/`.

---

## Your mission

Stand up a **real** microservice deployment, drive **reproducible faults** into it, and turn its raw telemetry into two clean things the rest of the team consumes:

1. **Telemetry adapters** (`C1`) — a query API over traces/metrics/logs/topology.
2. **`anomaly_score` + `FirstDeviationTime`** (`C2`) — the foundational per-node deviation primitives that *all three* scoring signals sit on top of.

Plus the **`GroundTruth`** (`C5`) that makes accuracy measurable.

You are the foundation. If your baseline is wrong, T/D/P are all wrong at once (see `Team_Split.md` §7.1). Treat `anomaly/` as the highest-care module in your lane.

---

## Modules you build

### 1. Testbed (`testbed/`) — PRD §6, spec §0

- Deploy the **OpenTelemetry Demo** (~15 services, multi-language, pre-instrumented, shared Redis/Kafka/Postgres) on K8s, emitting through the **existing Stage-1 OTel stack** (Tempo/Loki/Prometheus/Grafana). Helm values + install docs.
- Confirm all three signals land: traces in Tempo, logs in Loki, metrics in Prometheus. This is your M1 "green light."
- **Chaos Mesh** installed; author the scenario catalog as declarative CRDs (each experiment *is* the ground truth):

  | # | Scenario | Injection (spec §4) | True root |
  |---|---|---|---|
  | S0 | Downstream cascade (control) | flagd `productCatalogFailure` | product-catalog |
  | S1 | Slow-burn leaf | `StressChaos` memory ramp on cart **Redis** | redis/leaf |
  | S2 | Retry storm | `NetworkChaos` delay on a sync dep | delayed callee |
  | S3 | Clock skew | `TimeChaos` on one pod | faulted node |
  | S4 | Shared-dep confound | `NetworkChaos`/`StressChaos` on **Kafka** | kafka |
  | S5 | Infra-rooted | `StressChaos` CPU/mem on a K8s node | the node |

- **Dual-injection D1–D3** (spec §7) for Dev B's isolation eval — two simultaneous faults: disjoint (D1), shared-dep (D2), near-simultaneous onset within `Δ` (D3).

### 2. Ground-truth harness (`testbed/`) — spec §0, contract `C5`

Wrap each chaos run so it emits, out-of-band, one record:
```
Incident = { incident_id, true_root_node, fault_class, inject_start, inject_end }
```
The engine must **never** read this before scoring. Your harness writes it to where Dev C's eval and Dev B's isolation eval pick it up. Automate: "apply chaos CRD → stamp inject window → tear down → write Incident row."

### 3. Ingestion + adapters (`ingest/`) — PRD §4/§8, contract `C1`

- **Typed telemetry envelope** — spans / metric points / log records kept **distinct** (signal-specific), not flattened into one blob (PRD §4 box 1).
- **Causality-safe ordering:** trace DAG > logical clock > wall-clock fallback (matters for S3 clock skew).
- **Query adapters** over the stack: `traces(window)`, `metrics(node,m,window)`, `logs(node,window)`, `topology()` — the `call_type: sync|async|runs-on` annotation on edges comes from span kind/attributes; you provide the raw edges, Dev B orients them.
- **RCA trigger** — Prometheus-rule / threshold anomaly that opens an analysis window (PRD §4). Fires the engine.
- State: incident/RCA rows in **Postgres** (PRD §9); raw telemetry stays as references in the stack.

### 4. Anomaly primitives (`anomaly/`) — spec §1.1, contract `C2` ★ your crown jewel

- **Baseline learning (warmup):** per node, per metric (`p95_latency`, `error_rate`, `cpu`, `retry_rate`, `saturation`), collect a healthy window and store **robust** stats — `median μ_m` and `MAD s_m = 1.4826·MAD`. Robust so the fault itself can't poison the baseline.
- **Per-metric deviation** (one-sided — only increases are anomalous):
  ```
  z_m(t)   = max(0, (x_m(t) - μ_m) / (s_m + ε))
  dev_m(t) = 1 - exp(-z_m(t)/κ)            # squash to [0,1], κ=3
  ```
- **Composite:** `anomaly_score(node,t) = max over m of dev_m(t)` (max, not weighted sum — a deadlock spikes latency while CPU looks fine). Retain `dev_vector = {m: dev_m}` for Dev C's classification + explainability.
- **`FirstDeviationTime(node)`** = earliest `t` with `anomaly_score > θ_fire` for `k_persist` consecutive windows (`θ_fire=0.5`, `k_persist=3`). Returns `null` for slow-burn nodes that never cross — that null is expected and handled downstream (spec §1.1 known-gap flag); don't fake a value.
- Expose the secondary threshold `θ_low` (used by Dev B's subgraph inclusion + isolation gap-repair).

---

## Contracts you produce

| Contract | Consumers | Where |
|---|---|---|
| `C1` telemetry adapters | Dev B (graphs), Dev C (classification logs) | `contracts/` |
| `C2` AnomalyOutput | Dev B (all scoring) | `contracts/` |
| `C5` GroundTruth | Dev B (isolation eval), Dev C (accuracy) | `contracts/` |

You consume **nothing** from B or C — you're the source. This is why you can move fastest early; get the golden fixture + real S0 out the door so B and C are unblocked.

---

## Milestone plan

- **Week 0:** co-author `contracts/` + `fixtures/golden_incident` with B and C. Your job: make the fixture `AnomalyOutput` realistic (real MAD numbers, a slow-burn null).
- **M1:** OTel Demo + stack live; **S0** wired end-to-end; ground-truth harness working; **warmup baseline captured**. Deliver the real S0 `AnomalyOutput` so B validates graphs on real data early.
- **M2:** finish `anomaly_score`, `FirstDeviationTime`, and all three adapters against the live cluster. Anomaly module frozen and reviewed.
- **M3:** author the full **S0–S5** catalog; run **100+ incidents** across scenarios × injection sites × repeats; hand the corpus to Dev C. *Headline metric depends on this volume.*
- **M3.5:** author **D1–D3** dual-injection chaos for Dev B.
- **Stretch (M4+):** a second app (Online Boutique) as a held-out topology (spec §3.1), and more injection sites to grow N.

---

## Definition of done

- [ ] `metrics/traces/logs/topology(...)` return correct data for any window on the live cluster.
- [ ] Warmup baseline is robust: injecting a fault does **not** shift `μ_m`/`s_m` (spot-check MAD stability).
- [ ] `anomaly_score` ∈ [0,1], `max`-composite, matches the fixture within tolerance.
- [ ] `FirstDeviationTime` returns `null` for the fixture's slow-burn node and a correct timestamp for the sharp one.
- [ ] Each chaos run auto-produces a `GroundTruth` row the engine can't see pre-scoring.
- [ ] 100+ incident corpus handed to Dev C with per-incident telemetry retrievable.

---

## Watch-outs specific to your lane

- **The baseline is load-bearing, not preprocessing.** Bad `μ_m`/`s_m` → every signal degrades together. Get warmup window length right; verify robustness by injecting a spike into the warmup data and confirming the baseline barely moves.
- **S3 (clock skew) attacks your ordering directly.** Your causality-safe ordering (trace DAG first) is what makes `FirstDeviationTime` survive a skewed pod. Test it explicitly.
- **`null` FirstDeviationTime is a feature.** Slow-burn origins (S1) legitimately never cross `θ_fire`. Don't paper over it — Dev C measures it as *subgraph recall*.
- **Emergent, not authored.** Never hand-tune telemetry to make a scenario "work." The whole credibility of the project (PRD §6 anti-circularity) rests on telemetry being emergent from real chaos.
