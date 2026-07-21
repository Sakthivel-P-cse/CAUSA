# Golden incident (Dev C fixture smoke)

Hand-authored ~6-node incident so eval/classify/remediation build without Dev B.

| File | Role |
|---|---|
| `root_cause_candidate.json` | Flat C3 `RankedResult` (preferred) |
| `ranked_result.json` | Legacy clustered shape (still loadable) |
| `anomaly.json` | C2 node scores → `AnomalyBag` |
| `ground_truth.json` | C5 |
| `dual_ground_truth.json` | Eval-only isolation labels |
| `logs.json` | Classification input |
| `subgraph.json` | Opaque subgraph stand-in for FixtureScorer |

At A+B integration, prefer the shared `incident-detection/fixtures/golden_incident/`
artifact and drop this parallel golden or keep it as a second scenario only.
