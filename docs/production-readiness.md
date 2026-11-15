# Production readiness gates

Causa must remain proposal-only until all gates below are satisfied.

## RCA evidence

- A versioned labeled corpus covers every supported fault class, alert family
  and representative topology.
- Training, calibration and holdout incidents are disjoint.
- Each automatically remediable action has at least 30 calibration incidents;
  larger and balanced samples are strongly preferred.
- Holdout reports publish top-1, top-3, mean reciprocal rank, abstention
  precision/recall, expected calibration error and time-to-diagnosis.
- Baseline, dataset, weights, calibration and code commit are versioned as one
  release unit.

The runtime rejects automatic execution when a non-constant calibration map
with the required sample count is absent.

## Operational gates

- Dashboard and trigger endpoints are restricted to trusted networks by ingress,
  firewall or service-mesh policy.
- Remediation uses the dedicated database role and Kubernetes service account.
- Images are referenced by immutable release tag or digest.
- Readiness checks verify required persistence.
- Backup/restore and two-worker lease-expiry drills pass.
- A canary failure proves that rollback failure is surfaced rather than
  reported as success.

## Corpus workflow

Run clean baselines with every chaos flag disabled, execute each scenario
multiple times across different loads and topologies, capture operator-confirmed
ground truth with labeler, verification time and topology provenance, then
export the joined rows and run:

```sh
python -m eval.corpus_pipeline \
  --input eval/corpus/labeled-incidents.jsonl \
  --out eval/artifacts/fit_result.json
```

The command refuses to emit a release-ready artifact unless every
fault/alert/topology stratum has at least 30 calibration examples and 10
holdout examples. Never hand-edit fitted weights or calibration mappings.

Run the Postgres concurrency drill with
`scripts/drill-remediation-leases.sh`. It uses two database sessions to verify
`SKIP LOCKED`, forced-expiry takeover and stale-owner rejection, and removes
its uniquely named synthetic row on exit.
