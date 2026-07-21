# Golden incident fixture

One small, hand-authored incident that lets **all three lanes build without
waiting on a live cluster** (Team_Split §4). Co-owned by A/B/C; changes need
both sides of a seam to approve.

The incident: a **~6-node** slice of the OTel Demo where **product-catalog**
is the root cause (an S0-style `productCatalogFailure`). Errors cascade
frontend ← checkout ← product-catalog; **valkey-cart** (the cart cache) is a
**slow-burn** node that never crosses `θ_fire` — its `first_deviation` is `null`,
on purpose, so Dev C can exercise subgraph-recall.

All node names are **otel-demo** names (`product-catalog`, `checkout`,
`currency`, `cart`, `frontend`, `valkey-cart`) so they join cleanly to the
scenario catalog — no hipstershop/`*service` chimera. The types line up with the
contracts: each `anomaly_output.json` node is a `contracts.AnomalyOutput`
(`node`, `anomaly_score`, `dev_vector`, `first_deviation`), and `telemetry.json`
round-trips via `IncidentTelemetry.from_fixture(...)`.

| File | Contract | Consumer |
|---|---|---|
| `telemetry.json` | C1 | shape of the 6-node graph + `call_type`/`runs-on` edges (`IncidentTelemetry.from_fixture`) |
| `anomaly_output.json` | C2 | Dev B (scoring) — real MAD-derived scores; one slow-burn `null` |
| `root_cause_candidate.json` | C3 | Dev C (eval/baselines) — expected ranked output |
| `ground_truth.json` | C5 | Dev C (accuracy), Dev B (isolation) — never read pre-scoring |
| `logs.json` | C1 (narrow) | Dev C classify — AppLogic panic/exception on product-catalog |
| `dual_ground_truth.json` | C5 eval-only | Isolation ARI/cluster-count smoke (engine never reads) |
| `recorded/tempo_trace.json` | — | golden OTLP payload for parsing tests |

CI asserts **fixture smoke** on this single incident (subgraph recall +
FixtureFull / MostAnomalous / AB Scorer top-1). Portfolio headline metrics across
N×S0–S5 remain the M3 deliverable once the chaos corpus feeds the same harness.

