# RCA Improvement Analysis

The current RCA result (4% confidence, `ContainerMemoryNearLimit`, root cause `flagd`) is technically valid but weak. There are **five distinct layers** where improvements can be made.

---

## Layer 1 — Alert Rules (Why the wrong alert fires)

**Root cause of the problem:** `ServiceHighErrorRate` uses only Tempo service-graph span metrics:

```yaml
- alert: ServiceHighErrorRate
  expr: |
    label_replace(
      (
        sum by (server) (rate(traces_service_graph_request_failed_total[5m]))
        /
        sum by (server) (rate(traces_service_graph_request_total[5m]))
      ) > 0.05,
```

`productCatalogFailure` fails **one specific product SKU** — most requests still succeed — so this ratio never crosses 5%. The alert never fires for `product-catalog`.

**What to add:** A secondary HTTP-level alert using `http_server_request_duration_seconds_count` (already present in Prometheus):

```yaml
- alert: ServiceHighErrorRateHTTP
  expr: |
    sum by (service_name) (rate(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m]))
    /
    sum by (service_name) (rate(http_server_request_duration_seconds_count[5m]))
    > 0.03
  for: 2m
```

Also consider lowering the `for: 2m` duration for AppLogic inject demos — the fault produces a transient burst but the alert needs the condition sustained.

---

## Layer 2 — Baseline Quality (Why confidence is 4%)

The confidence formula:

```
confidence = score × margin / score
```

If `flagd` scored 0.08 and the next candidate scored 0.06, the margin is only 0.02 — tiny margin = tiny confidence.

**Why the margin is small:**

- Baselines were captured with a 30-minute window that included cluster startup noise. The backup `flagd-applogic.backup.json` shows `emailMemoryLeak=1000x`, `kafkaQueueProblems`, `imageSlowLoad=10sec` etc. were already active. Healthy and unhealthy signals are mixed.
- Only 41 baselines for 49 discovered services = incomplete coverage.

**How to fix:**

1. Clear all flags before capturing baselines:
   ```bash
   ./inject-applogic.sh --clear
   ./inject-issues.sh --clear
   ```
2. Wait ~5 minutes for metrics to settle.
3. Re-capture with a longer window (60 min instead of 30):
   ```bash
   cd /home/sakthi/projects/causa && source .venv/bin/activate
   export PGHOST=127.0.0.1 PGPORT=15432 PGDATABASE=causa PGUSER=causa PGPASSWORD=causa
   PYTHONPATH=. python -m testbed.harness.capture_baseline \
     --namespace otel-demo --window-minutes 60 \
     --out baselines/otel-demo.json --write-db \
     --prometheus-url http://127.0.0.1:9090 \
     --tempo-url http://127.0.0.1:3200 \
     --loki-url http://127.0.0.1:3100
   ```
4. Verify no chaos flags are active during capture.

---

## Layer 3 — Scoring Weights (Equal thirds, not tuned)

Current defaults: `T = D = P = 1/3`

- **T is the strongest signal** for AppLogic — flagd changed its behavior first, clearly preceding all shop services by ~315s.
- **P is weak** — `productCatalogFailure` doesn't produce consistent propagation chains because it affects one SKU, not all calls.

**What to try:** Write a `fit_result.json` at `eval/artifacts/fit_result.json`:

```json
{
  "best_weights": {"w_T": 0.5, "w_D": 0.35, "w_P": 0.15}
}
```

The weight loader validates and normalizes these automatically. Weights toward `T` and `D` better match temporal AppLogic faults.

---

## Layer 4 — Temporal Horizon Too Short (60s default)

The T signal normalizes time against a **60-second default horizon** (`H_DEFAULT = 60.0` in `scoring/signals.py`).

The UI showed `flagd showed anomalous behaviour about 315s before services` — but the horizon is only 60s. Everything after 60s gets clamped to T≈0.0, so services that deviated at 61s and 315s look equally "late". Temporal discrimination across the graph collapses.

For memory pressure incidents that take 5–10 minutes to evolve, a 60s horizon loses all ranking power. Ideally `H_DEFAULT` should adapt per alert type, or be set to at least 300s for container-resource alerts.

---

## Layer 5 — No Calibration Map

Without a calibration artifact (`eval/artifacts/fit_result.json` with a `calibration.mapping` section), raw confidence is used directly. Raw confidence from the margin formula is inherently low when many candidates cluster close in score — which happens when baselines are noisy. A proper calibration map (from running labeled incidents through the eval/fit harness) remaps raw scores to better-calibrated probabilities.

---

## Summary Table

| Layer | Current state | Improvement |
|-------|--------------|-------------|
| Alert rules | Only trace service-graph metrics, 5% threshold | Add HTTP 5xx alert; lower `for:` duration |
| Baselines | Captured with chaos flags on, 30-min window | Re-capture clean, 60-min window |
| Weights | Equal thirds (T=D=P=0.33) | Tune toward T=0.5, D=0.35, P=0.15 |
| Temporal horizon | 60s hard default | Increase to 300–600s or per-alert-type |
| Calibration | Missing — raw confidence used | Run fit harness to generate `fit_result.json` |

## Recommended Order of Changes

1. **Baselines** — highest leverage, no code change, fixes confidence immediately.
2. **Alert rules** — add HTTP 5xx alert so AppLogic inject produces the right incident type.
3. **Weights** — write `fit_result.json` to bias toward T signal.
4. **Temporal horizon** — code change in `scoring/signals.py`, increase `H_DEFAULT` to 300.
5. **Calibration** — run the eval fit harness after accumulating labeled incidents.

Fixing (1) and (2) alone would likely bring confidence from ~4% on a memory alert to 30–50%+ on the right error/latency alert.
