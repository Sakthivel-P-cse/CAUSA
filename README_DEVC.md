# Dev C — Outputs (eval / classify / remediation)

Owns everything downstream of a ranked RCA result.

## Layout

| Path | Purpose |
|---|---|
| `contracts/` | Shared C1–C5 seams (all-three review) |
| `fixtures/golden_incident/` | Golden fixture — build without waiting on Dev B |
| `eval/` | Harness, baselines, ablation, tuning, calibration, isolation, reports |
| `classify/` | Fault taxonomy + confusion matrix |
| `remediation/` | Router (3 deep), CLI approval, simulated canary |
| `artifacts/` | Generated demo outputs |
| `tests/test_dev_c.py` | Lane tests / CI gate |

## Quick start

```bash
pip install -r requirements-devc.txt
PYTHONPATH=. python -m eval.runner          # golden CI gate
PYTHONPATH=. pytest -q tests/test_dev_c.py
PYTHONPATH=. python -m eval.report          # write artifacts/
PYTHONPATH=. python -m remediation.cli --auto-approve
PYTHONPATH=. python -m remediation.cli --auto-approve --breach   # canary rollback demo
```

## Contracts Dev C consumes

- **C3** `RootCauseCandidate` / multi-cluster `RankedResult` + abstention
- **C4** `Scorer` (ablation / tuning) — `eval.fixture_scorer.FixtureScorer` until B lands
- **C5** single-fault `Incident` + dual-injection GT for ARI
- **C1** logs (classification) — fixture `logs.json` until A's adapter is wired
- **C2** `anomaly_score` for MostAnomalous baseline
