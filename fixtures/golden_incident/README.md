# Golden incident fixture

Hand-authored ~6-node incident used so Dev C can build eval / classify / remediation
without waiting on Dev B's scorer.

| File | Contract |
|---|---|
| `subgraph.json` | topology for the incident subgraph |
| `anomaly.json` | C2 `AnomalyOutput` (MostAnomalous baseline) |
| `ranked_result.json` | C3 `RankedResult` (known-correct ranking) |
| `ground_truth.json` | C5 single-fault `Incident` |
| `dual_ground_truth.json` | C5 dual-injection sample for isolation metrics |
| `logs.json` | C1-style log signatures for classification |

True root: **cart-redis** (Dependency / S1 slow-burn leaf shape).
