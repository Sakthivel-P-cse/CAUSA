# Dev C — Outputs (eval / classify / remediation)

Owns everything downstream of a ranked RCA result. Communicates with Dev A/B
**only through `contracts/`** (plus thin `adapters/` at the boundary).

## Layout

| Path | Purpose |
|---|---|
| `contracts/` | Shared C1–C5 seams (stdlib-only; all-three review) |
| `adapters/` | Boundary translators (fixture/AB shapes → contracts) |
| `fixtures/golden_incident/` | Golden fixture smoke input |
| `eval/` | Harness, baselines, ablation, tuning, calibration, isolation, reports |
| `classify/` | Fault taxonomy + confusion matrix |
| `remediation/` | Router (3 deep), CLI approval, simulated canary |
| `artifacts/` | Regenerated **fixture smoke** outputs (not portfolio claims) |
| `tests/test_dev_c.py` | Lane tests / CI gate |

## Architecture

```
contracts/          ← pure DTOs + Scorer/Logs ports (no lane imports)
adapters/           ← shape translation only
eval/ classify/ remediation/   ← Dev C application layer (depends on contracts)
fixtures/           ← load JSON into contracts via adapters
```

`FixtureScorer` implements the C4 `Scorer` port against the golden fixture until
Dev B's real scorer is wired. It does not import `graphs/` or `scoring/`.

## Quick start

```bash
pip install -r requirements-devc.txt
PYTHONPATH=. python -m eval.runner          # golden fixture smoke gate
PYTHONPATH=. pytest -q tests/test_dev_c.py
PYTHONPATH=. python -m eval.report          # write fixture-smoke artifacts/
PYTHONPATH=. python -m remediation.cli --auto-approve
PYTHONPATH=. python -m remediation.cli --auto-approve --breach
```

## Contracts Dev C consumes

- **C3** flat `RankedResult.candidates` + `abstain_reason`
- **C4** `Scorer` port — `Weights.to_signal_dict()` / `EnabledSignals.to_set()` for AB
- **C5** `Incident` (`fault_class` str-tolerant) + eval-only dual GT
- **C1** logs via `adapters.logs` (fixture today; A’s envelopes at integration)
- **C2** per-node `AnomalyOutput` + eval `AnomalyBag` for MostAnomalous

## Honesty

CI and `eval.report` run on **n=1 golden fixture** only. That is a smoke gate,
not the multi-incident headline metric.
