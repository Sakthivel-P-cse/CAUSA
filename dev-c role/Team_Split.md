# Causa — Team Split (3 Developers)

> **Companions:** `PRD2.md` (product scope), `RCA_Engine_Spec.md` (scoring math + eval protocol).
> **Per-dev briefs:** `Dev_A.md` (Inputs), `Dev_B.md` (Core), `Dev_C.md` (Outputs).
> **Read this first.** It defines the lanes, the contracts that connect them, and the git workflow that lets three people build in parallel without stepping on each other.

---

## 1. The cut principle

Coupling in Causa is **layered, not uniform**:

- **Horizontally** (telemetry → RCA → remediation) the seams are loose — each layer talks to the next through a small, stable data contract. These loose seams become the **person boundaries**.
- **Vertically inside the RCA engine** (Influence Graph → T/D/P → arbitration, all sitting on `anomaly_score`) the coupling is tight and inherent — the three signals are one graph viewed three ways. This tight kernel stays **inside one head** (Dev B).

So the split is by **horizontal layer**, and the one indivisible cluster is kept whole.

```
   Dev A  ─────────▶  Dev B  ─────────▶  Dev C
   INPUTS             CORE               OUTPUTS
   testbed + data     graphs + scoring   eval + remediation
   + anomaly_score    (the star)         + classification
        │                  │                   │
        └── AnomalyOutput ─┘                   │
        └── Telemetry adapters ────────────────┤
                           └── RootCauseCandidate ┘
        └── GroundTruth ───────────────────────┘
```

---

## 2. Lane summary

| | **Dev A — Inputs** | **Dev B — Core** ★ | **Dev C — Outputs** |
|---|---|---|---|
| **Owns (PRD/spec)** | PRD §4 boxes 1+trigger, §6, §8 ingestion; spec §0, §1.1, §4 (authoring), §7 (chaos only) | PRD §5.1–5.2, §8 RCA; spec §1.2, §2, §7 (algorithm) | PRD §3, §5.3, §7, §8 remediation; spec §3, §5, §6 |
| **Core deliverable (M1–M3)** | Real testbed + telemetry adapters + chaos ground truth + `anomaly_score` | 4 graphs + Influence Graph + T/D/P + arbitration + output schema | Eval harness + baselines + ablation + calibration + classification + remediation router |
| **Stretch (M4+)** | Second app (cross-topology), more injection sites | **Multi-fault isolation (§7)** | Designed confounders C1–C3, richer canary |
| **Directories** | `testbed/ ingest/ anomaly/` | `graphs/ scoring/ isolation/` | `eval/ classify/ remediation/` |
| **Produces contract** | `AnomalyOutput`, telemetry adapters, `GroundTruth` | `RootCauseCandidate`, `Scorer` interface | (terminal consumer) |

---

## 3. Shared contracts (the seams)

These are the **only** cross-dev interfaces. They live in `contracts/` and change **only by a PR all three approve**. Freeze them in week 0; touch them rarely. Everything else is private to a lane.

### C1 — Telemetry adapters (A → B, A → C)
A read API over the OTel stack. B builds graphs from traces; C reads logs for classification.
```
traces(window)   -> [ Span{ trace_id, span_id, parent_id, service, kind, start, end, attributes } ]
metrics(node, m, window) -> timeseries          # p95_latency, error_rate, cpu, retry_rate, saturation
logs(node, window)       -> [ LogRecord ]
topology()       -> [ Edge{ src, dst, call_type: sync|async|runs-on } ]   # infra + service edges
```

### C2 — AnomalyOutput (A → B)
The foundational primitives from spec §1.1. Everything in scoring consumes these.
```
anomaly(node, window) -> { anomaly_score: float[0,1], dev_vector: { metric: float } }
first_deviation(node) -> timestamp | null        # null = slow-burn, never crossed θ_fire
thresholds            -> { θ_fire: 0.5, k_persist: 3, θ_low: <secondary> }
```

### C3 — RootCauseCandidate (B → C)
The scorer's output, spec §2.5. C consumes it for eval, calibration, classification, remediation.
```
RootCauseCandidate = {
  node_id, node_kind,                 # service | infra | dependency
  score, confidence,
  role,                               # origin | amplifier | victim | victim-of-infra
  signals: { T, D, P },
  dev_vector,
  explanation
}
RankedResult = { incident_id, candidates: [RootCauseCandidate], fault_class? }
```

### C4 — Scorer interface (B, called by C)
So C's tuning/ablation harness can drive B's scorer without importing its internals.
```
score(incident_subgraph, weights={w_T,w_D,w_P}, enabled_signals={T,D,P}) -> RankedResult
```
`enabled_signals` lets C run the ablation table (drop one signal, single-signal baselines) against the *real* scorer — no reimplementation.

### C5 — GroundTruth (A → B, A → C)
spec §0. C scores accuracy against it; B validates isolation against dual-injection truth.
```
Incident = { incident_id, true_root_node, fault_class, inject_start, inject_end }
```

> **The engine never sees `GroundTruth` until output is compared.** A's harness writes it out-of-band from the Chaos Mesh experiment; the scorer reads only telemetry.

---

## 4. Fixtures — how nobody waits on anybody

The blocking risk ("I can't start until your part exists") is killed by **golden fixtures** committed in week 0 to `fixtures/`:

- `fixtures/golden_incident/` — one small hand-authored incident: a ~6-node graph, an `AnomalyOutput` for it, a `RootCauseCandidate` result, and its `GroundTruth`. Known root cause.

With it:
- **B** builds graphs + T/D/P against the fixture `AnomalyOutput` — doesn't wait for A's real pipeline.
- **C** builds the entire eval harness + baselines + classification + router against fixture `RootCauseCandidate`s — doesn't wait for B's scorer. (Random / MostAnomalous baselines don't need B at all.)

When each lane swaps its mock for real code, **the eval harness on `main` is the integration test** — accuracy numbers climbing is the signal the seams are wired right.

---

## 5. Git workflow

**Directory ownership = disjoint files = near-zero merge conflicts.**
```
testbed/      ingest/      anomaly/       ← Dev A
graphs/       scoring/     isolation/     ← Dev B
eval/         classify/    remediation/   ← Dev C
contracts/    fixtures/                   ← shared, change-by-agreement
```

- **Trunk-based.** Short-lived feature branches → PR into `main`. `main` always green.
- **CI gate = the eval harness** runs on the golden fixture on every PR. A red harness blocks merge.
- **Seam PRs need seam reviewers.** Any PR touching `contracts/` → all three approve. Any PR touching a `fixtures/` file → both sides of that seam approve.
- **Within-lane PRs** need one review (any teammate) — keeps velocity up while spreading knowledge.
- **Branch naming:** `a/<thing>`, `b/<thing>`, `c/<thing>` so ownership is visible in `git branch`.

---

## 6. Milestone matrix (who does what, when)

From PRD §10. Critical path is **M1 → M3**; if only those land, the project still has a defensible, provable core.

| Milestone | Dev A | Dev B | Dev C |
|---|---|---|---|
| **Week 0** | Co-author `contracts/` + `fixtures/golden_incident` (all three, together) | " | " |
| **M1** Testbed + telemetry + graphs | Deploy OTel Demo + stack; wire **S0** end-to-end; ground-truth harness; capture warmup baseline | Build 4 graphs + **Influence Graph** from adapters/fixtures | Scaffold eval harness on fixtures; Random/MostAnomalous baselines |
| **M2** Deterministic scorer | Finish `anomaly_score` + `FirstDeviationTime` + all 3 adapters | **T/D/P + arbitration + schema + explanations** | Ablation harness + per-signal baselines against B's `Scorer` |
| **M3** Eval + tuning ★ | Author full chaos catalog **S0–S5**; run 100+ incidents | Support weight tuning; fix scorer from ablation findings | **top-k/MRR/subgraph-recall + calibration + tuning + site-split + classification** — *headline lands* |
| **M3.5** Isolation | Author dual-injection **D1–D3** chaos | **Four-stage multi-fault isolation (§7)** | Isolation metrics: cluster-count, per-fault top-k, ARI |
| **M4** Remediation router | — | (isolation polish) | **Router 3-deep** (rollback / config-patch / code-fix PR) + routing table |
| **M5** Approval + canary | — | — | **CLI approval + simulated canary** |
| **M6** Polish + writeup | Per-scenario run data, cost notes | Kernel writeup, explanation examples | Reliability diagram, confusion matrix, demo recording |

---

## 7. The three watch-outs

1. **`anomaly_score` is load-bearing for everything.** It's upstream of T, D, and P simultaneously. A bad baseline poisons all three signals at once. Dev A's warmup/MAD work is not "preprocessing" — it's foundational. Review it hard.
2. **The RCA engine is one dev, period.** Dev B owns graphs + Influence Graph + T/D/P + arbitration + isolation as a single, un-split lane — the three signals are one graph viewed three ways and cannot be cleanly divided across people. If capacity is short, add a 4th dev to **Dev A's or Dev C's** lane (more chaos sites, richer eval, the remediation surface) — never carve a second person into the RCA core.
3. **Weight tuning is a shared ritual.** C owns the tuning *harness*; B owns the *weights being tuned*. They meet at the `Scorer` interface (C4). Tune on the train split only; no weight ever sees its test incident (spec §3).
