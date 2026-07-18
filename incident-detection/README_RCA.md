# Causa RCA kernel

Dev B graphs, deterministic scoring, and isolation integrated behind Dev A's
canonical C1/C2 contracts.

## Pipeline

```text
IncidentTelemetry + list[AnomalyOutput]
  → rca.adapter
  → topology + traces + KernelAnomaly
  → four graphs + Incident Subgraph
  → Influence Graph (IG)
  → T/D/P → layer arbitration
  → RankedResult
```

The canonical C1, C2, and C5 contracts remain in `contracts/telemetry.py`,
`contracts/anomaly.py`, and `contracts/ground_truth.py`. C3 output types live in
`contracts/rca.py`; kernel-only graph and aggregate types are internal.

## Tests

Dev A's OTel golden fixture exercises the adapter contract. Dev B's synthetic
S0, S1, S5, and D1–D3 fixtures remain kernel-level tests under `tests/rca/`.
