# Dev C — Outputs: Evaluation, Classification, Remediation

> **Lane:** everything downstream of a ranked result — proving it's accurate, labeling it, and acting on it through a human gate. You own the project's **headline metric** and the demo's closing loop.
> **Companions:** `Team_Split.md` (contracts + git), `PRD2.md` §3/§5.3/§7/§8, `RCA_Engine_Spec.md` §3/§5/§6.
> **You own directories:** `eval/`, `classify/`, `remediation/`.

---

## Your mission

Two halves:

1. **Prove it** — the evaluation harness that turns `RootCauseCandidate`s + `GroundTruth` into the credible portfolio claim: *"correct root cause in top-3 X% of the time across N incidents and M scenarios, here's exactly where it fails, and here's how it beats every baseline."*
2. **Act on it** — classify the top candidate's fault, route the matching remediation, and close the loop through a human-gated CLI + simulated canary.

Almost everything you build consumes the `RootCauseCandidate` contract (`C3`) and `GroundTruth` (`C5`) — which means **you can build most of your lane against fixtures before Dev B's scorer exists.** Random/MostAnomalous baselines don't need B at all.

---

## Modules you build

### 1. Eval harness (`eval/`) — PRD §3, spec §3/§5 ★ the headline

- **Runner:** feed each incident's telemetry through the engine, collect `RankedResult`, compare to `GroundTruth`.
- **Metrics:** `top-1/3/5` accuracy, **MRR**, and **subgraph recall** (was `true_root_node` even *included* for scoring? — caps top-k independent of ranking; spec §5). Report **per-scenario** (S0–S5) *and* aggregate — the hard cells (S1, S4) must be visible, not averaged away (spec §4).
- **Baselines** (spec §3) — report alongside the headline, all via Dev B's `Scorer` interface (`C4`):

  | Baseline | Rule |
  |---|---|
  | Random | shuffle subgraph |
  | MostAnomalous | sort by `anomaly_score` |
  | EarliestOnly | T alone |
  | CascadeOnly | D alone |
  | **Full** | S(N) + arbitration |

- **Ablation table:** drop one signal at a time + each signal alone, via `enabled_signals`. If `EarliestOnly ≈ Full`, the other signals are decoration — surface that *before* the demo.

### 2. Weight tuning + splits (`eval/`) — spec §3/§3.1

- **Train/test split over chaos scenarios** (injection site + fault params), **not** time windows. Tune `w_T,w_D,w_P` (grid/coordinate search) on train only; report every number on held-out test. **No weight ever sees its test incident** — this is the integrity guarantee of the whole claim.
- **Cross-topology (§3.1):** hold out a disjoint set of *injection sites within the app* (deep chain vs. wide fan-out vs. leaf origin) from tuning; report top-k on held-out sites. State the analytical argument too (only 3 global weights can overfit).
- Tuning drives Dev B's `Scorer` through `C4` — you own the *harness*, B owns the *weights*. Meet at the interface (`Team_Split.md` §7.3).

### 3. Confidence calibration (`eval/`) — spec §5

`raw_conf = S(top)·(S(top)−S(second))/(S(top)+ε)` is a score, not a probability. Bin over test incidents, plot predicted vs **empirical top-1 correctness** → **reliability diagram**; fit isotonic/Platt on train, apply on test, report **ECE**. Feed the calibrated number back into `RootCauseCandidate.confidence`.

### 4. Failure classification (`classify/`) — PRD §5.3, spec §6.4

Classify the top candidate into `AppLogic | Config | Infra | Dependency | Resource | Data` from its `dev_vector`, `node_kind`, `role`, and log signatures (via Dev A's `logs()` adapter). This is the bridge into the remediation router — you own both, so routing and the label that drives it stay cohesive.

- **Measured as a real metric** (natural confounding): score against chaos `fault_class` ground truth, emit a **per-class confusion matrix**. The naturally-hard confusions (Dependency↔Resource, Config↔AppLogic) must be visible, not averaged.
- **Stretch (deferred):** designed confounders **C1–C3** (bad flag → spin = Config-as-Resource, etc.) — specced in spec §6.4, not built for V1.

### 5. Remediation router (`remediation/`) — PRD §7, spec §6.1

Route on `fault_class`. Build **3 branches deep**, the rest as routing-table stubs (depth over surface):

| Class | Remediation | Depth |
|---|---|---|
| App Logic | LLM opens a **PR** (proposed diff + evidence), **never auto-merged** | deep |
| Config | config-patch proposal (before/after) | deep |
| Dependency | **rollback-to-last-good** / circuit-break | deep |
| Infra | scale/restart/cordon | table stub |
| Resource | scale-up proposal | table stub |
| Data | alert-only | table stub |

### 6. Human approval + canary (`remediation/`) — PRD §8, spec §6.2

- **CLI approval surface:** show ranked candidates + evidence + proposed remediation; approve → proceed, reject → next candidate. Web UI deferred (adds no conceptual value).
- **Apply + canary:** thin wrapper / **simulated** progressive rollout — watch SLOs, auto-rollback on breach (Argo Rollouts or a simulation). This closes the demo loop.

### 7. Isolation metrics (`eval/`) — spec §7, M3.5

For Dev B's dual-injection D1–D3: **cluster-count accuracy** (right *number* of faults?), **per-fault top-k** (does each true root top exactly one cluster?), and **Adjusted Rand Index** of node→fault assignment vs. ground truth.

---

## Contracts

| You consume | From |
|---|---|
| `C3` RootCauseCandidate, `C4` Scorer | Dev B |
| `C1` logs adapter (classification) | Dev A |
| `C5` GroundTruth (all accuracy) | Dev A |

You produce the **terminal artifacts** — metrics, confusion matrix, reliability diagram, PRs/patches, approval decisions. Nobody downstream of you.

---

## Milestone plan

- **Week 0:** co-author `contracts/` + fixture. Push for a `RootCauseCandidate` shape you can evaluate *and* route on.
- **M1:** scaffold the eval runner + Random/MostAnomalous baselines against the **fixture** — no wait on B.
- **M2:** ablation + per-signal baselines against B's real `Scorer`; subgraph-recall metric.
- **M3:** full metrics (top-k/MRR/recall) + **weight tuning** + **calibration** + **site-split** + **classification** over Dev A's 100+ incident corpus. **★ Headline metric lands here.**
- **M3.5:** isolation metrics (cluster-count, per-fault top-k, ARI) on D1–D3.
- **M4:** remediation router, 3 branches deep + routing table.
- **M5:** CLI approval + simulated canary — close the loop.
- **M6:** reliability diagram, confusion matrix, per-scenario table, demo recording.

---

## Definition of done

- [ ] Eval runner produces top-1/3/5, MRR, subgraph recall — per scenario and aggregate.
- [ ] Every headline number is reported next to Random/MostAnomalous/per-signal baselines + an ablation row.
- [ ] Train/test split is over scenarios; verified no weight touched its test incident.
- [ ] Reliability diagram + ECE produced; `confidence` field is the calibrated value.
- [ ] Confusion matrix over 6 fault classes; hard confusions visible.
- [ ] Router opens a real (unmerged) PR on an AppLogic fault and proposes a config patch on a Config fault.
- [ ] CLI shows evidence + approve/reject; simulated canary rolls back on SLO breach.
- [ ] (M3.5) ARI + cluster-count reported on D1–D3.

---

## Watch-outs specific to your lane

- **Subgraph recall gates everything.** Top-k is *conditional* on the true root being in the subgraph. If recall is low, no scoring improvement can help — report it first, and push the finding back to Dev B (subgraph inclusion) rather than chasing ranking.
- **Tuning integrity is the credibility of the claim.** One leaked test incident and the headline is worthless. Keep the split airtight and documented.
- **You own the label *and* the router that trusts it.** A wrong `fault_class` sends a good root cause to the wrong remediation. The confusion matrix isn't just a metric — it's your router's error budget.
- **Baselines are cheap and brutal.** MostAnomalous is a one-liner and a strong baseline. If Full barely beats it, that's the most important thing you'll learn — surface it early, don't bury it.
- **Depth over surface (spec §6.1).** Three convincing remediation branches beat six shallow ones. Don't spread thin to fill the table.
