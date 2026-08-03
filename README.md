# Causa

Causa is an observability-driven root-cause analysis system for Kubernetes
microservices. It ingests Prometheus metrics, Tempo traces and Loki logs,
constructs an incident influence graph, ranks root-cause candidates and can
prepare remediation plans for explicit operator approval.

## Safety status

Causa is suitable for development and controlled evaluation. Automatic
remediation is disabled by default and remains blocked unless a validated
confidence-calibration artifact with at least 30 labeled incidents is loaded.
Do not enable automatic execution until the evaluation requirements in
[`docs/production-readiness.md`](docs/production-readiness.md) are satisfied.

## Architecture

```text
Alertmanager
  → causa-ingest (scope, snapshot, durable incident/outbox)
  → stage2-rca (anomaly, graph, isolation, ranking)
  → Postgres
  → dashboard
  → proposed plan → approver → leased remediation worker
```

The Python packages are organized by domain (`incidents/`, `rca/`,
`remediation/`, `shared/`, …); the FastAPI/React product dashboard is under
`dashboard/`; Kubernetes charts live next to their domain under each `helm/`
subdirectory.

## Local development

1. Copy `.env.example` to `.env` and configure the integrations you use.
2. Start the Causa stack with `./causa-startup.sh`.
3. Start the OpenTelemetry demo with `./microservice-startup.sh`.

Normal demo startup no longer deletes the application namespace. Set
`RECREATE_DEMO_NAMESPACE=1` only when a destructive clean reinstall is intended.

## Verification

```bash
PYTHONPATH=.import:. .venv/bin/pytest -q

cd dashboard/frontend
npm ci
npm run lint
npm test
npm run build

cd ../..
bash scripts/check-prom-rules.sh
```

See [`SECURITY.md`](SECURITY.md) before exposing any component outside a local
or isolated cluster.
