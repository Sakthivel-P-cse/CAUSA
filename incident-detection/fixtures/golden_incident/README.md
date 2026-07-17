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
scenario catalog — no hipstershop/`*service` chimera. This holds in the recorded
trace too: `recorded/tempo_trace.json` sets `service.name`/`net.peer.name` to the
otel-demo names (not `checkoutservice`/`productcatalogservice`). The types line up
with the contracts: each `anomaly_output.json` node is a `contracts.AnomalyOutput`
(`node`, `anomaly_score`, `dev_vector`, `first_deviation`), and `telemetry.json`
round-trips via `IncidentTelemetry.from_fixture(...)`.

| File | Contract | Consumer |
|---|---|---|
| `telemetry.json` | C1 | shape of the 6-node graph + `call_type`/`runs-on` edges (`IncidentTelemetry.from_fixture`) |
| `anomaly_output.json` | C2 | Dev B (scoring) — real MAD-derived scores; one slow-burn `null` |
| `root_cause_candidate.json` | C3 | Dev C (eval/baselines) — expected ranked output |
| `ground_truth.json` | C5 | Dev C (accuracy), Dev B (isolation) — never read pre-scoring |
| `baseline.json` | C2 | robust median/MAD per `(node, metric)`; `anomaly.load_baselines(...)` → all lanes score against identical baselines |
| `recorded/tempo_trace.json` | — | golden OTLP payload for parsing tests |

Numbers here are illustrative-but-consistent: the origin scores highest, victims
lower, and the leaf is a `null` first-deviation. The **T signals in
`root_cause_candidate.json` are computed exactly from `anomaly_output.json`'s
`first_deviation` per spec §2.1** — `t0 = 12:04:00`, `horizon = min(max_delta, H=60) = 40s`,
`T = clamp01(1 − (FDT − t0)/horizon)` → product-catalog `1.0`, checkout `0.5`,
frontend `0.0` — so the fixture never contradicts the scoring math. `baseline.json`
is a plausible healthy stand-in (not raw incident values); regenerate every file
from a real S0 run (`testbed/harness/run_scenario.py`) once the cluster is up.
