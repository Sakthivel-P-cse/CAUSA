# C4 scorer interface

The scoring seam exposes the real RCA scorer to downstream callers without
requiring them to reimplement T/D/P.

## Call shape

```python
from scoring.scorer import score, Scorer, DEFAULT_WEIGHTS

result = score(
    subgraph,                          # IncidentSubgraph
    weights={"T": 1/3, "D": 1/3, "P": 1/3},  # DEFAULT_WEIGHTS
    enabled_signals={"T", "D", "P"},    # ablation subset
)
# result: RankedResult(incident_id, candidates, abstain_reason?, fault_class?)
```

Or via the thin wrapper:

```python
Scorer().score(subgraph, weights=..., enabled_signals=...)
```

## Rules

| Concern | Behavior |
|---|---|
| Default weights | `1/3` each for T, D, P |
| Disabled signals | Zeroed **inside** the scorer; weights for disabled keys are ignored |
| Determinism | Same inputs → same ranked list (safe for sweeps) |
| Arbitration | On by default (`run_arbitration=True`); disable for pure linear ablation |
| Confidence | Raw §5 formula stamped on every candidate |
| Tuning logic | Deliberately outside the kernel |

## Ablation

Call `score` once per non-empty subset of `{T, D, P}` and once with each
single signal alone. Compare against Full + arbitration. See
`tests/rca/test_scorer_ablation.py` and
`tests/rca/test_scorer_weight_sweep.py` for kernel sanity checks.
