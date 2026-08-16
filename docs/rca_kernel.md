# RCA Kernel Writeup (Dev B / M6)

Deterministic path from one incident's telemetry to a ranked, explainable
list of probable root-cause nodes. Owned packages: `graphs/`, `scoring/`,
`isolation/`. Contracts in `contracts/` (C1–C5).

## Pipeline

```
C1 topology() + traces(window)     C2 AnomalyOutput
            \                         /
             \                       /
              v                     v
     ┌─────────────────────────────────────┐
     │  Four graphs                        │
     │  · Infrastructure (persistent)      │
     │  · Service Dependency (persistent)  │
     │  · Trace Execution (on-trigger)     │
     │  · Incident Subgraph (on-trigger)   │
     └──────────────────┬──────────────────┘
                        v
              Influence Graph (IG)
              sync reverse · async keep · runs-on infra→svc
                        v
              T / D / P on IG   (weights default 1/3)
                        v
              Layer arbitration (infra vs hosted service)
                        v
              C3 RootCauseCandidate[]  (+ raw confidence)
                        │
                        └── isolation/ (stretch): 4-stage multi-fault
```

## Edge orientation (why the leaf bug dies)

| `call_type` | Failure influence | In IG |
|---|---|---|
| sync (HTTP/gRPC/DB) | callee → caller | **reverse** |
| async / event | producer → consumer | **keep** |
| infra `runs-on` | infra → service | orient `infra → service` |

A root-cause DB is a *sink* in the call graph (no outgoing calls). After
sync reversal it gains outgoing influence edges to every caller, so `D`
can score it as an origin. No leaf prior — orientation was the bug.

Verified on golden / S0: `cart-db` has `out_degree > 0` in IG and ranks
as `role=origin`.

## Scoring (spec §2)

```
S(N) = w_T·T(N) + w_D·D(N) + w_P·P(N)
```

- **T** — `t0 = min FirstDeviationTime` over subgraph nodes; slow-burn → `T=0.5` flagged.
- **D** — pure `OutInfluence` / `InInfluence` = Σ anomaly of successors / predecessors (no degree fudge).
- **P** — consistent only when temporal order **and** `edge.degraded` (no partial credit).
- **Arbitration** — post-score re-rank: promote infra above hosted service when infra leads on time + anomaly; annotate `victim-of-infra`.
- **Confidence** — raw `S(top)·(S(top)−S(second))/(S(top)+ε)` (calibration = Dev C).

C4 entrypoint: `scoring.scorer.score` — see [`docs/scorer_c4.md`](scorer_c4.md).

## Explanation examples (live fixture runs)

**Golden (DB origin):**
> cart-db deviated 2.50s before its influence targets (T=1.00), influence flows outward to 1 node(s) with no incoming influence edges (D=1.00), 1/1 outgoing edges consistent in order (P=1.00). Role: origin.

**S0 (downstream cascade control):**
> cart-db deviated 2.00s before its influence targets (T=1.00), influence flows outward to 1 node(s) with no incoming influence edges (D=1.00), 1/1 outgoing edges consistent in order (P=1.00). Role: origin.

**S5 (infra-rooted after arbitration):**
> node-b deviated 3.00s before its influence targets (T=1.00), influence flows outward to 1 node(s) with no incoming influence edges (D=1.00), 1/1 outgoing edges consistent in order (P=1.00). Role: origin.
>
> catalog … Role: victim-of-infra.  *(hosted service demoted under node-b)*

## Fixtures (offline DoD)

| Fixture | Stresses | Expect |
|---|---|---|
| `fixtures/golden_incident/` | Week-0 seam | `cart-db` top origin |
| S0 | clear DB cascade | origin role on `cart-db` |
| S1 / **S5** | infra-rooted | `victim-of-infra` + infra above service |
| D1 | disjoint dual fault | cluster count 2 |
| D2 | shared downstream dep | cluster count 2 (Stage 3) |
| D3 | onsets within Δ, adjacent | abstain `MULTI-INCIDENT — ambiguous` |

Run: `.venv/bin/python -m pytest tests/ -v`

## Blocked on Dev A / Dev C

| Item | Owner | Status |
|---|---|---|
| Live OTel S0 redis / Chaos Mesh S0–S5 | Dev A | fixture-verified; swap adapters when A lands |
| Live dual-injection D1–D3 chaos | Dev A | fixture-verified; GroundTruth swap-in |
| Weight tuning harness + isotonic/ECE | Dev C | C4 seam ready; B does not own harness |
| Stage 2 Docker/HTTP service wiring | (out of B dirs) | out of scope for this finish |
