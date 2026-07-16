# Online Boutique — held-out topology (spec §3.1)

Google Online Boutique is the **held-out second topology** for the cross-topology
generalization claim: a genuinely different graph shape (gRPC-only, Redis cart,
**no Kafka**) on which the scorer's weights are **never tuned**. If top-k holds
here, the claim isn't a restatement of the training score.

This is the **stretch** target (M4+), not the primary testbed — the OTel Demo
carries S0–S5/D1–D3 because S4 needs Kafka.

## Wiring (reuses the existing script)

Boutique is installed out-of-band (upstream `microservices-demo`), then its
telemetry is routed into the Causa agent tier by the existing script — no new
code:

```bash
# Boutique is assumed installed in ns gcp-demo (kind context kind-gcp-demo-test).
scripts/patch_gcp_demo_telemetry.sh --namespace gcp-demo
```

## Chaos re-map

The S-catalog re-parameterized to Boutique service names (no Kafka → **S4 N/A**):

| Scenario | OTel Demo target | Boutique target |
|---|---|---|
| S0 cascade | product-catalog | productcatalogservice |
| S1 slow-burn | valkey-cart | redis-cart |
| S2 retry storm | product-catalog | productcatalogservice |
| S3 clock skew | checkout | checkoutservice |
| S4 shared-dep | kafka | **N/A (no Kafka)** |
| S5 node stress | k8s node | k8s node |

Copy `../chaos/S*.yaml`, swap the `labelSelectors` + `causa/true-root` labels to
the Boutique names, and set `namespaces: [gcp-demo]`. Run through the same
`harness/run_scenario.py` (`--namespace gcp-demo --context kind-gcp-demo-test`).
Capture a **separate** Boutique baseline — do not reuse the OTel Demo's.
