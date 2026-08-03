# Dev C — Outputs (eval / classify / remediation)

Owns everything downstream of a ranked RCA result. Communicates with Dev A/B
**only through `contracts/`** (plus thin `adapters/` at the boundary).

Integrated under `./` on `feature/dev-abc-integration`.

## Layout

| Path | Purpose |
|---|---|
| `contracts/` | Shared C1–C5 seams (stdlib-only; A+B canonical) |
| `adapters/` | Boundary translators (fixture/AB shapes → contracts; `ABScorer`) |
| `fixtures/golden_incident/` | Shared golden (`golden-0001`, root=`product-catalog`) |
| `eval/` | Harness, baselines, ablation, tuning, calibration, isolation, reports |
| `classify/` | Fault taxonomy + confusion matrix |
| `remediation/` | Router (3 deep), CLI approval, simulated canary |
| `artifacts/` | Regenerated **fixture smoke** outputs (not portfolio claims) |
| `tests/test_dev_c.py` | Lane tests / CI gate |

## Honesty

CI and `eval.report` run on **n=1 golden fixture** only (`product-catalog` / AppLogic).
That is a smoke gate (subgraph recall + FixtureFull/MostAnomalous top-1), **not** the
multi-incident S0–S5 portfolio headline — that remains the M3 deliverable once the
chaos corpus feeds the same harness.

## Quick start

```bash
cd .
pip install -e ".[dev]"
PYTHONPATH=. python -m eval.runner
PYTHONPATH=. pytest -q tests/test_dev_c.py
PYTHONPATH=. python -m remediation.cli --auto-approve
PYTHONPATH=. python -m remediation.cli --auto-approve --breach
```
