# Causa testbed (contract C5)

A **real deployment + chaos** — telemetry is emergent (real retries, clock skew,
backpressure), the chaos experiment gives clean ground truth (PRD2 §6). This is
what defuses the circularity objection: *we* don't author the propagation.

## Layout

```
otel-demo/     PRIMARY testbed — OTel Demo Helm values + install guide (has Kafka → S4)
chaos/         Chaos Mesh CRDs: S0–S5 (single) + D1–D3 (dual-injection)
harness/       ground-truth harness (apply → stamp → teardown → write Incident)
boutique/      HELD-OUT topology — Google Online Boutique (cross-topology, spec §3.1)
```

## The two apps

| | OTel Demo (primary) | Online Boutique (held-out) |
|---|---|---|
| Role | full catalog S0–S5, D1–D3; the headline metric | cross-topology generalization (spec §3.1) |
| Kafka | yes → **S4 works** | no → S4 N/A |
| Weights | tuned here | **never** tuned here (a genuine held-out shape) |
| Wiring | `otel-demo/values.yaml` | `scripts/patch_gcp_demo_telemetry.sh` (existing) |

## End-to-end flow

```bash
# 0. Stage-1 stack up
scripts/deploy.sh dev

# 1. Deploy the demo (see otel-demo/install.md) and confirm the M1 green light
#    (traces in Tempo, logs in Loki, service-graph metrics in Prometheus).

# 2. Capture a HEALTHY warmup baseline (before any chaos)
python -m testbed.harness.capture_baseline \
  --namespace otel-demo --window-minutes 30 --out baselines/otel-demo.json

# 3. Run a scenario end-to-end (injects → Stage-1 alerts → causa-ingest opens
#    the incident → Stage-2 scores → ground truth is written out-of-band)
python -m testbed.harness.run_scenario --scenario S2 --hold-seconds 300 \
  --namespace otel-demo --write-db

# 4. Build the corpus: many scenarios × sites × repeats → 100+ incidents
for s in S0 S1 S2 S3 S4 S5; do
  python -m testbed.harness.run_scenario --scenario $s --write-db --out ground_truth/
done
```

## Ground-truth isolation (invariant)

`run_scenario.py` writes `Incident{incident_id, true_root_node, fault_class,
inject_start, inject_end}` to `groundtruth.ground_truth`. The Stage-2 engine
connects as the `rca_engine` Postgres role, which has **no privileges** on that
schema (see `helm/postgres` init.sql) — so the engine physically cannot read
injected truth before its output is scored. Enforced by grants, not convention.

## Notes / caveats (stated, not hidden)

- **Chaos label selectors** (`app.kubernetes.io/name: …`) must match your
  deployed demo's labels — reconcile with `kubectl -n otel-demo get pods --show-labels`.
- **S0 is not Chaos Mesh** — its canonical form is the flagd `productCatalogFailure`
  feature flag; the harness applies an HTTPChaos stand-in and warns.
- **`scripts/e2e_all_services_rca.sh`** (scale-zero) stays as a smoke test of the
  trigger→RCA→Postgres loop — it is an availability fault, NOT part of the S-catalog corpus.
- **N is bounded by wall-clock** (real chaos runs cost minutes) — report whatever
  the time budget yields (target 100+), per scenario, not just the aggregate.
