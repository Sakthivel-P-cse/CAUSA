# Dev B — Core: Graphs + Deterministic Scoring ★

> **Lane:** the RCA engine — the star deliverable. The one part of Causa that is a single coherent, indivisible artifact.
> **Companions:** `Team_Split.md` (contracts + git), `PRD2.md` §5, `RCA_Engine_Spec.md` §1.2/§2/§7, `KnowledgeBase/Graphs/*`.
> **You own directories:** `graphs/`, `scoring/`, `isolation/`.

---

## Your mission

Turn one incident's telemetry into a **ranked, explainable list of probable root-cause nodes**, deterministically. Given `AnomalyOutput` (from Dev A) over a topology, build the graphs, re-orient them into the **Influence Graph**, compute three signals **T/D/P**, arbitrate across layers, and emit `RootCauseCandidate`s.

This lane is the tight kernel from `Team_Split.md` §1 — T, D, and P are one graph viewed three ways. *

## Modules you build

### 1. Graph construction (`graphs/`) — PRD §5.1, spec §0, `KnowledgeBase/Graphs/*`

Four graphs:

| Graph | Lifetime | Node = | Purpose |
|---|---|---|---|
| Infrastructure | Persistent | K8s node / pod / DB / cache / LB / external API | Rule out platform failure first |
| Service Dependency | Persistent | One logical service | Blast radius, propagation, origin |
| Trace Execution | On-trigger | One span (DAG) | First-failure + critical path in a request |
| Incident Subgraph | On-trigger | Anomalous nodes only | Shrink 100s of services → the few that matter |

- Build from Dev A's `topology()` + `traces()` adapters (`C1`).
- **Incident Subgraph inclusion** (spec §1.1 / Incident Subgraph.md): a node is in if `anomaly_score > θ_fire`, OR it's connected to an anomalous node, OR it participated in anomalous traces, OR it shows resource deviation in-window. Use `θ_low` for connectivity inclusion so slow-burn origins aren't filtered out — this directly caps Dev C's *subgraph recall*.
- Annotate each edge `call_type: sync | async | runs-on` from span kind/attributes.

### 2. Influence Graph (`graphs/`) — spec §1.2 ★ highest-leverage component

Re-orient every edge into the direction failure actually propagates:

| `call_type` | Influence flows | In `IG` |
|---|---|---|
| sync (HTTP/gRPC/DB) | callee → caller | **reverse** edge |
| async/event (Kafka, queue) | producer → consumer | **keep** edge |
| infra `runs-on` | infra → service | orient `infra → service` |

Build `IG` once per incident, **infra + service nodes unified**. This is what dissolves the D-at-leaves bug *by construction*: a root-cause DB is a call-graph sink, but in `IG` its sync edges reverse, so it gains outgoing influence to every caller and can score as an origin. **Get this right and everything downstream works; get it wrong and all three signals degrade together.** This is the single riskiest file in the project — write it first, test it hardest.

### 3. The three signals (`scoring/`) — spec §2

```
S(N) = w_T·T(N) + w_D·D(N) + w_P·P(N)      # weights start 1/3, tuned by Dev C on train split
```

- **T — temporal primacy (§2.1):** time-delta, not rank.
  `T(N) = clamp01(1 - (FirstDeviationTime(N) - t0)/(horizon+ε))`, `horizon = min(max_subgraph_delta, H=60s)`. No-deviation (slow-burn) node → `T=0.5` neutral, flagged.
- **D — cascade direction (§2.2), on `IG`:**
  `D(N) = (α·anomaly_score(N) + OutInfluence(N)) / (α·anomaly_score(N) + OutInfluence(N) + InInfluence(N) + ε)`, `α=0.5`. Derive **`role`** here: origin (high D, high anomaly) / amplifier (mid D, high betweenness + incoming) / victim (low D).
- **P — propagation consistency (§2.3), on `IG`:** fraction of outgoing influence edges where `N deviated before M` AND the edge metric degraded, anomaly-weighted. Zero out-degree → `P=0`, flagged.

### 4. Layer arbitration (`scoring/`) — spec §2.4 (the corrected "L")

**Not a 4th signal — a post-scoring re-ranking pass.** After all `S(N)` are computed, for each service `N` and its hosting infra `I` (`I→N` in `IG`): if both are top-C candidates, `FirstDeviationTime(I) < FirstDeviationTime(N)`, and `anomaly_score(I) ≥ anomaly_score(N)` → promote `I` above `N`, annotate `N.role = victim-of-infra`. Does the one thing a per-node summand can't: reorder two *different* nodes.

### 5. Output + explainability (`scoring/`) — spec §2.5/§2.6, contract `C3`

Emit `RootCauseCandidate` (schema in `Team_Split.md` C3), sorted by score post-arbitration. Every candidate carries its `{T,D,P}` sub-scores, `dev_vector`, `role`, and a **generated sentence**:
> *"cart-db deviated 4.2s before its callers (T=0.96), influence flows outward to 3 services with no incoming anomaly (D=0.91), 3/3 outgoing edges degraded in order (P=1.0). Role: origin."*

Explainability is **mandatory**, not optional polish (PRD §5.2).

### 6. Scorer interface (`scoring/`) — contract `C4`

Expose `score(subgraph, weights, enabled_signals) -> RankedResult` so Dev C's tuning + ablation harness drives your real scorer with varying weights / dropped signals — no reimplementation on their side.

### 7. Multi-fault isolation (`isolation/`) — spec §7 — **your stretch, M3.5**

Four stages on `IG` restricted to the incident subgraph: (1) connected components → cheap first cut; (2) gap-repair merges false splits via quiet-relay paths (θ_low + monotonic time); (3) origin-clustering splits false merges via temporal-source origins + coupling window `Δ` + causal reachability; (4) independent §2 scoring per cluster. **Abstain** (`MULTI-INCIDENT — ambiguous`) when faults are causally adjacent rather than guess. Reaches into scoring internals — this is why it's yours, not a separable lane.

---

## Contracts

| You consume | From |
|---|---|
| `C1` adapters, `C2` AnomalyOutput | Dev A |
| `C5` GroundTruth (isolation eval only) | Dev A |

| You produce | For |
|---|---|
| `C3` RootCauseCandidate | Dev C |
| `C4` Scorer interface | Dev C |

---

## Milestone plan

- **Week 0:** co-author `contracts/` (own C3 + C4 shapes) + fixture.
- **M1:** build the 4 graphs + **Influence Graph** against the fixture, then against Dev A's real S0. Prove the leaf-bug fix on S1's redis node manually (it should gain outgoing `IG` edges).
- **M2:** T/D/P + arbitration + output schema + explanation strings. Expose the `Scorer` interface. Score S0/S1 end-to-end.
- **M3:** support Dev C's weight tuning; act on ablation findings (if `EarliestOnly ≈ Full`, investigate why D/P aren't earning their weight). *Headline lands on C's harness over your scorer.*
- **M3.5:** build isolation; validate against D1–D3.
- **M6:** kernel writeup + explanation examples for the demo.

---

## Definition of done

- [ ] `IG` reversal verified on a real sync leaf (DB gains outgoing influence edges).
- [ ] T uses time-delta; slow-burn node scores `T=0.5`, not 0 or 1.
- [ ] D scores a true origin high even when its victims are still ramping (self-term α works).
- [ ] `role` is populated and matches intuition on S0 (origin) and S5 (victim-of-infra after arbitration).
- [ ] Every candidate has a non-empty, accurate explanation sentence.
- [ ] `Scorer` runs with arbitrary weights + dropped signals for Dev C's ablation.
- [ ] (M3.5) isolation returns the right *number* of clusters on D1/D2 and abstains on D3.

---

## Watch-outs specific to your lane

- **The Influence Graph is the whole ballgame.** Every signal is defined on it. A wrong reversal silently corrupts T-ordering interpretation, D direction, and P consistency simultaneously. Unit-test edge orientation per `call_type` before writing any signal.
- **The engine never spans two people.** It is one dev's lane by design — the signals are too tightly coupled to divide. Extra capacity goes to Dev A/C, never a second hand in T/D/P.
- **Isolation ≠ a clean downstream module.** It re-runs your scoring per cluster and reads subgraph internals — keep it in `isolation/` but know it's coupled to `scoring/`. Don't promise it as separable.
- **Neutral, not zero.** Slow-burn `T=0.5` and zero-out-degree `P=0` are *flagged neutrals* — a node must be able to earn rank via the other signals. Penalizing them re-introduces the leaf bug.
