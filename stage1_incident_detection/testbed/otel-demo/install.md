# Deploy the OpenTelemetry Demo as the Causa testbed

Prereqs: the Stage-1 stack is up (`scripts/deploy.sh dev`) — the demo exports
into `otel-agent`, so the agent tier must exist first.

```bash
# 1. Namespace
kubectl create namespace otel-demo

# 2. Add the chart repo
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo update

# 3. Install with the Causa values (disables bundled observability,
#    routes OTLP to the otel-agent tier)
helm upgrade --install otel-demo open-telemetry/opentelemetry-demo \
  --namespace otel-demo \
  -f testbed/otel-demo/values.yaml \
  --wait --timeout 10m
```

## Verify the M1 green light — all three signals landing

```bash
# traces in Tempo
kubectl -n tempo port-forward svc/tempo 3200:3200 &
curl -s 'http://localhost:3200/api/search?tags=service.name=frontend&limit=1' | jq '.traces | length'

# metrics: the service graph must include demo services
kubectl -n prometheus port-forward svc/prometheus 9090:9090 &
curl -s 'http://localhost:9090/api/v1/query?query=traces_service_graph_request_total' \
  | jq '.data.result[].metric.server' | sort -u | head

# logs in Loki
kubectl -n loki port-forward svc/loki 3100:3100 &
curl -sG 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service_name="frontend"}' | jq '.data.result | length'
```

When all three return data, capture a warmup baseline (healthy window) before
injecting any chaos:

```bash
python -m testbed.harness.run_scenario --list         # see the catalog
python -m testbed.harness.capture_baseline \
  --namespace otel-demo --window-minutes 30 --out baselines/otel-demo.json
```

> **Version note:** the demo chart's value keys (`default.env`, component
> `.enabled` toggles) shift between releases. Pin a version and reconcile
> `values.yaml` against `helm show values open-telemetry/opentelemetry-demo`.
