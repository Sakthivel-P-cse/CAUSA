# Onboarding any application to Causa

Causa discovers services, topology, and baselines from live OTLP telemetry. You do not need a per-app profile file.

## What you configure once per cluster install

Set these on the **causa-ingest** Helm release (see `incidents/helm/causa-ingest/values.yaml`):

| Setting | Env var | Purpose |
|--------|---------|---------|
| `monitoredApp.namespace` | `CAUSA_APP_NAMESPACE` | Kubernetes namespace of the workload (container CPU/memory PromQL) |
| `monitoredApp.podLabelSelector` (optional) | `CAUSA_POD_LABEL_SELECTOR` | PromQL pod matcher when standard `app` / `app.kubernetes.io/name` labels do not apply |
| Observability backends | `PROMETHEUS_URL`, `TEMPO_URL`, `LOKI_URL` | Where ingest reads traces, metrics, and logs |

Optional tuning (not required for first traffic):

- `CAUSA_PROFILE=generic` (default) — no app-specific JSON profile needed
- `CAUSA_MONITORED_SERVICES_REGEX` / `CAUSA_EXCLUDED_SERVICES_REGEX` — narrow scope
- `REMEDIATION_SERVICE_ALLOWLIST` — restrict remediation to named services

## Point your app at Causa

Instrument microservices with OpenTelemetry and export traces (and metrics if available) to your cluster collector. The collector should forward to Tempo/Prometheus (including the **service graph** connector metrics when possible).

Minimum signal for discovery:

- `service.name` on spans
- Parent/child span relationships (or service graph `traces_service_graph_*` in Prometheus)

## Alertmanager webhooks

Route firing alerts to causa-ingest `/trigger`. Namespace and pod selector can override the install default via webhook labels:

- `namespace` or `causa_namespace` / `causa_app_namespace`
- `causa_pod_label_selector` / `causa_label_selector` (optional)

Service identity is taken from common Alertmanager labels (`service`, `app`, `app_kubernetes_io_name`, etc.).

## Baselines

On first observation of a `service.name`, ingest schedules an automatic baseline capture (`CAUSA_AUTO_BASELINE`, default on). A periodic CronJob refreshes baselines from Prometheus service-graph activity.

Manual refresh (debug):

```bash
PROMETHEUS_URL=http://127.0.0.1:9090 python scripts/capture_live_baselines.py
```

## Optional profile overrides

`shared/profiles/otel-demo.json` is an **example** of extra remediation capabilities for the OpenTelemetry Demo. Copy the pattern only if you need per-service remediation tuning; discovery does not depend on it.
