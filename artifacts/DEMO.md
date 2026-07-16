# Causa Dev C — Demo artifacts

## Headline (golden fixture)
- FixtureFull top-1: 1.0
- MostAnomalous top-1: 1.0
- Subgraph recall: 1.0

## Ablation
See `ablation_table.txt`.

## Calibration
- ECE: 0.0000
See `reliability_diagram.txt`.

## Classification
- Golden predicted=Dependency truth=Dependency
See `confusion_matrix.txt`.

## Isolation
- D1 ARI: 1.000
- D3 abstention correct: 1.0

## Cross-topology
T/D/P are computed from local graph structure and live deviations; the scorer memorizes nothing per-node. The only tunable parameters are three global weights (w_T, w_D, w_P) — a 3-dimensional surface with negligible overfit risk relative to learned node embeddings. In-app site split (deep chain / wide fan-out / leaf origin) is therefore a genuine within-app generalization check, not a restatement of train score.

## Remediation
- AppLogic → proposed PR (unmerged)
- Config → before/after patch
- Dependency → rollback-to-last-good
- Canary rollback on breach: True
