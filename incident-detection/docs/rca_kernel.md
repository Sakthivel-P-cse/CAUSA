# RCA Kernel Writeup (Dev B / M6)

Deterministic path from one incident's telemetry to a ranked, explainable
list of probable root-cause nodes. Owned packages: `graphs/`, `scoring/`,
`isolation/`. Canonical C1/C2 input is converted at `rca.adapter`; C3 output is
defined in `contracts/rca.py`.

## Pipeline

```
Canonical IncidentTelemetry     Canonical AnomalyOutput[]
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

Verified on synthetic S0: `cart-db` has `out_degree > 0` in IG and ranks as
`role=origin`. The canonical OTel golden fixture verifies the A-to-B adapter.

## Scoring (spec §2)

```
S(N) = w_T·T(N) + w_D·D(N) + w_P·P(N)
```

- **T** — `t0 = min FirstDeviationTime` over subgraph nodes; slow-burn → `T=0.5` flagged.
- **D** — pure `OutInfluence` / `InInfluence` = Σ anomaly of successors / predecessors (no degree fudge).
- **P** — consistent only when temporal order **and** `edge.degraded` (no partial credit).
- **Arbitration** — post-score re-rank: promote infra above hosted service when infra leads on time + anomaly; annotate `victim-of-infra`.
- **Confidence** — raw `S(top)·(S(top)−S(second))/(S(top)+ε)`.

C4 entrypoint: `scoring.scorer.score` — see [`docs/scorer_c4.md`](scorer_c4.md).

## Explanation examples (live fixture runs)

**S0 (downstream cascade control):**
> cart-db deviated 2.00s before its influence targets (T=1.00), influence flows outward to 1 node(s) with no incoming influence edges (D=1.00), 1/1 outgoing edges consistent in order (P=1.00). Role: origin.

**S5 (infra-rooted after arbitration):**
> node-b deviated 3.00s before its influence targets (T=1.00), influence flows outward to 1 node(s) with no incoming influence edges (D=1.00), 1/1 outgoing edges consistent in order (P=1.00). Role: origin.
>
> catalog … Role: victim-of-infra.  *(hosted service demoted under node-b)*

## Fixtures (offline DoD)

| Fixture | Stresses | Expect |
|---|---|---|
| `fixtures/golden_incident/` | Canonical OTel seam | adapter contract |
| S0 | clear DB cascade | origin role on `cart-db` |
| S1 / **S5** | infra-rooted | `victim-of-infra` + infra above service |
| D1 | disjoint dual fault | cluster count 2 |
| D2 | shared downstream dep | cluster count 2 (Stage 3) |
| D3 | onsets within Δ, adjacent | abstain `MULTI-INCIDENT — ambiguous` |

Run: `python -m pytest tests/rca -v`

## Deferred to later integration phases

| Item | Owner | Status |
|---|---|---|
| Live OTel S0 redis / Chaos Mesh S0–S5 | fixture-verified; cluster testing deferred |
| Live dual-injection D1–D3 chaos | fixture-verified; cluster testing deferred |
| Stage 2 Docker/HTTP service wiring | planned after contract integration |
