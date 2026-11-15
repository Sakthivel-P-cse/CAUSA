# Causa — Session Handoff Document
**Date:** 2026-07-25 (Saturday, 12:02 AM IST)
**Branch:** `feature/dev-abc-integration`
**Cluster:** `kind-gcp-demo-test` (Kind, single-node)

---

## Current State — Everything Running

### Startup commands (run if starting fresh)

```bash
cd /home/sakthi/projects/causa
./causa-startup.sh          # Causa stack + port-forwards + API (:8090) + UI (:5174)
./microservice-startup.sh   # OTel demo in otel-demo namespace
# Then manually port-forward OTel demo storefront:
kubectl --context kind-gcp-demo-test -n otel-demo port-forward svc/frontend-proxy 8080:8080
```

### Active port-forwards (from `.run/*.pid`)

| Service | URL |
|---------|-----|
| Causa UI | http://127.0.0.1:5174 |
| Causa API | http://127.0.0.1:8090 |
| Prometheus | http://127.0.0.1:9090 |
| Tempo | http://127.0.0.1:3200 |
| Loki | http://127.0.0.1:3100 |
| Postgres | 127.0.0.1:15432 |
| OTel demo shop | http://127.0.0.1:8080 (manual PF needed) |

### Postgres access (for psql / capture_baseline)

```bash
export PGHOST=127.0.0.1 PGPORT=15432 PGDATABASE=causa PGUSER=causa PGPASSWORD=causa
```

---

## What Was Accomplished This Session

### 1. Full deployment validated
- Kind cluster `gcp-demo-test` running with all Causa + OTel demo pods healthy.
- All 20+ otel-demo pods: `Running`.
- Causa pods: `causa-ingest`, `stage2-rca` — both `Running`.

### 2. Baselines captured
- **41 baselines** written to `incident-detection/baselines/otel-demo.json`
- **41 rows** upserted into `anomaly_baseline` table in Postgres
- Command used:
  ```bash
  cd incident-detection && source .venv/bin/activate
  PYTHONPATH=. python -m testbed.harness.capture_baseline \
    --namespace otel-demo --window-minutes 30 \
    --out baselines/otel-demo.json --write-db \
    --prometheus-url http://127.0.0.1:9090 \
    --tempo-url http://127.0.0.1:3200 \
    --loki-url http://127.0.0.1:3100
  ```
- **Known issue:** baselines were captured while some chaos flags were already active (see inject status below). This makes them impure — see improvement plan.

### 3. End-to-end RCA pipeline confirmed working
- 35 RCA reports in Postgres.
- First non-abstained result observed:
  - **Alert:** `ContainerMemoryNearLimit` on `product-catalog`
  - **Root cause:** `flagd` (4.1% confidence)
  - **Impact:** currency, payment, product-catalog (affected); cart (amplifying)
- `ServiceTrafficSpike` on `otel` fired with **33.3% confidence** (self-origin, otel itself).
- Pipeline path confirmed: Prometheus → Alertmanager → causa-ingest → stage2-rca → Postgres → dashboard API → UI.

### 4. AppLogic fault injected
- `./inject-applogic.sh` run — all flags currently ON:
  - `productCatalogFailure=on`, `cartFailure=on`, `paymentFailure=90%`
  - `paymentUnreachable=on`, `adFailure=on`, `recommendationCacheFailure=on`
  - `llmInaccurateResponse=on`, `llmRateLimitError=on`, `loadGeneratorFloodHomepage=on`
- **Problem:** these AppLogic faults did not trigger `ServiceHighErrorRate` on shop services. Prometheus only fires `ContainerMemoryNearLimit` (memory pressure from load) and `ServiceHighErrorRate` on `flagd` itself (18% error rate on flagd, not product-catalog/cart/payment).
- **Root cause of mismatch:** alert rule uses `traces_service_graph_request_failed_total`, but `productCatalogFailure` fails one product SKU only — overall error rate stays below 5%.

### 5. RCA improvement analysis written
- Saved at: `rca-improvement.md`

---

## Current DB Snapshot

```
anomaly_baseline rows : 41
rca_reports rows      : 35

Latest 5 reports:
2026-07-24T17:54:54  ServiceTrafficSpike       otel             33.3%  root=otel
2026-07-24T17:54:53  ServiceTrafficSpike       product-reviews   0.0%  abstained
2026-07-24T17:43:55  ContainerMemoryNearLimit  checkout          0.8%  root=flagd
2026-07-24T17:30:56  ContainerMemoryNearLimit  product-catalog   4.1%  root=flagd
2026-07-24T17:15:50  ServiceHighLatency        checkout          0.0%  abstained (pre-baseline)
```

---

## Inject State (currently active — CLEAR BEFORE NEXT BASELINE CAPTURE)

```bash
./inject-applogic.sh --status
# productCatalogFailure = on *
# cartFailure = on *
# paymentFailure = 90% *
# paymentUnreachable = on *
# adFailure = on *
# recommendationCacheFailure = on *
# llmInaccurateResponse = on *
# llmRateLimitError = on *
# loadGeneratorFloodHomepage = on *
```

---

## Known Issues / Why RCA Confidence Is Low

| Issue | Detail |
|-------|--------|
| Baselines polluted | Captured during partial chaos — mixed healthy/unhealthy signal |
| Wrong alert type fires | AppLogic faults → memory alerts, not error-rate alerts |
| Alert threshold too high | `ServiceHighErrorRate` requires >5% error rate sustained 2m; narrow SKU fault never crosses it |
| Weights untuned | Equal thirds T=D=P=0.33; no `fit_result.json` written |
| Temporal horizon 60s | flagd led by 315s; anything beyond 60s collapses to T≈0 |
| No calibration map | Raw margin-based confidence, inherently low when candidates cluster |

---

## Next Steps (Priority Order)

### Step 1 — Clean up inject and re-capture baselines (highest impact, no code change)

```bash
cd /home/sakthi/projects/causa
./inject-applogic.sh --clear
./inject-issues.sh --clear   # if this was also run earlier
sleep 300  # wait 5 min for metrics to settle

cd incident-detection && source .venv/bin/activate
export PGHOST=127.0.0.1 PGPORT=15432 PGDATABASE=causa PGUSER=causa PGPASSWORD=causa
PYTHONPATH=. python -m testbed.harness.capture_baseline \
  --namespace otel-demo --window-minutes 60 \
  --out baselines/otel-demo.json --write-db \
  --prometheus-url http://127.0.0.1:9090 \
  --tempo-url http://127.0.0.1:3200 \
  --loki-url http://127.0.0.1:3100
```

### Step 2 — Add HTTP-level alert rule

Edit `incident-detection/helm/prometheus/templates/rules-configmap.yaml`.
Add under the `service-health` group, alongside `ServiceHighErrorRate`:

```yaml
- alert: ServiceHighErrorRateHTTP
  expr: |
    sum by (service_name) (
      rate(http_server_request_duration_seconds_count{http_response_status_code=~"5.."}[5m])
    )
    /
    sum by (service_name) (
      rate(http_server_request_duration_seconds_count[5m])
    )
    > 0.03
  for: 2m
  labels:
    severity: critical
    layer: service
    namespace: "otel-demo"
  annotations:
    summary: "High HTTP error rate on {{ $labels.service_name }}"
```

Then redeploy:
```bash
helm upgrade --install prometheus ./incident-detection/helm/prometheus \
  --namespace prometheus --kube-context kind-gcp-demo-test
```

### Step 3 — Tune scoring weights toward T signal

Write `incident-detection/eval/artifacts/fit_result.json`:
```json
{
  "best_weights": {"w_T": 0.5, "w_D": 0.35, "w_P": 0.15}
}
```

Restart stage2-rca pod to pick up the new weights:
```bash
kubectl --context kind-gcp-demo-test -n causa rollout restart deploy/stage2-rca
```

### Step 4 — Increase temporal horizon

In `incident-detection/scoring/signals.py`, change:
```python
H_DEFAULT = 60.0
```
to:
```python
H_DEFAULT = 300.0
```
Rebuild and redeploy stage2-rca.

### Step 5 — Re-inject a targeted fault and verify

After steps 1–4, do a clean targeted inject:
```bash
./inject-applogic.sh --target payment  # 90% payment failures
# wait 3–5 minutes
curl 'http://127.0.0.1:8090/api/incidents?limit=3&sort=newest'
# Open newest incident in UI: http://127.0.0.1:5174
```
Expected: `ServiceHighErrorRate` on `payment` or `flagd` with >15% confidence.

### Step 6 — Run eval fit harness (longer-term)

After accumulating 10+ labeled incidents:
```bash
cd incident-detection && source .venv/bin/activate
export PGHOST=127.0.0.1 PGPORT=15432 PGDATABASE=causa PGUSER=causa PGPASSWORD=causa
PYTHONPATH=. python -m eval.fit_from_live
```
This writes a `fit_result.json` with both optimized weights and a calibration map, which significantly improves confidence scores.

---

## File Locations

| File | Purpose |
|------|---------|
| `handoff.md` | This document |
| `rca-improvement.md` | Detailed RCA improvement analysis |
| `incident-detection/baselines/otel-demo.json` | Current (impure) baselines |
| `incident-detection/eval/artifacts/live_eval.json` | Existing eval artifact |
| `.run/flagd-applogic.backup.json` | Backup of flagd config before inject |
| `inject-applogic.sh` | AppLogic fault inject/clear/status |
| `inject-issues.sh` | Broader fault injection (heavier) |
| `causa-startup.sh` | Start full Causa stack |
| `microservice-startup.sh` | Start OTel demo |
