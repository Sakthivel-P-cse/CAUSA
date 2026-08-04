# Causa Dev C — Fixture smoke artifacts

> n=1 golden fixture only. Not a multi-incident headline claim.

## Golden fixture smoke
- FixtureFull top-1: 1.0
- MostAnomalous top-1: 1.0
- Subgraph recall: 1.0

## Ablation
See `ablation_table.txt` (FixtureScorer reweight of golden signals).

## Classification
- Predicted=AppLogic truth=AppLogic
See `confusion_matrix.txt`.

## Remediation (proposed payloads only)
- AppLogic → proposed PR payload (never auto-merged)
- Config → before/after patch proposal
- Dependency → rollback-to-last-good proposal
- Canary rollback on breach: True

