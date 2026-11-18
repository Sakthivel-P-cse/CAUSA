# Causa

**Find the signal. Follow the cause. Keep the operator in control.**

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)

Causa is an observability-driven root-cause analysis system for Kubernetes microservices. It brings metrics, traces, and logs together around an incident, builds an influence graph, ranks possible causes, and prepares remediation plans for operator review. The goal: make a noisy incident easier to investigate without handing over control of production.

> **Status:** Development and controlled evaluation. Automatic remediation is disabled by default. Keep Causa proposal-only until the calibration and operational gates in [production readiness](docs/production-readiness.md) are met. Read [SECURITY.md](SECURITY.md) before exposing components outside a local or isolated cluster.

## What it does

- Ingests Alertmanager alerts and telemetry from Prometheus, Tempo, and Loki.
- Detects anomalies and builds incident and influence graphs to rank cause candidates.
- Presents incidents, topology, metrics, logs, traces, and remediation review in a dashboard.
- Produces typed remediation plans with an approval and worker-based execution path.
- Includes an OpenTelemetry demo, fault scenarios, fixtures, and evaluation tooling.

## Tech stack

Python 3.11+ and FastAPI power the backend; React, Vite, React Query, React Flow, and Recharts power the dashboard. PostgreSQL stores incident and remediation state. Kubernetes, Helm, Prometheus, Tempo, Loki, and Alertmanager support deployment and observability. CI exercises Python and frontend checks with Python 3.12, Node 22, and PostgreSQL 16.

## Architecture

```text
Alertmanager → ingestion → anomaly detection / RCA graphs / ranking → PostgreSQL
                                                                      ↓
                                                dashboard → proposed plan
                                                                      ↓
                                                 approval → remediation worker
```

```text
.
├── incident-detection/       # Ingestion, RCA, remediation, evaluation, tests, and Helm charts
├── dashboard/                # FastAPI backend, React frontend, Dockerfile, and Helm chart
├── incidents/                # Incident ingestion deployment resources
├── demo-bugs/                # Demo service fault examples
├── scripts/                  # Infrastructure, schema, and operational helpers
├── tests/                    # Dashboard and integration-oriented Python tests
├── docs/                     # Readiness and operator guidance
├── causa-startup.sh          # Local Kind stack and dashboard startup
└── microservice-startup.sh   # OpenTelemetry demo startup
```

The RCA kernel and its contracts are described in [incident-detection/README_RCA.md](incident-detection/README_RCA.md).

## Getting started

### Prerequisites

For the local cluster: Docker, Kind, kubectl, Helm, Python 3.11+ with `venv`, Node.js and npm, `curl`, `lsof`, and `setsid`. CI uses Node 22. The startup scripts may download images and charts and deploy resources into the configured cluster.

### Configure

```bash
cp .env.example .env
```

**TODO:** Remove the stray `>>>>>>> 98bf5d4 ...` merge marker from your local `.env` before using it. The startup script sources this file as shell code, and the checked-in example currently contains that invalid line. Keep real credentials out of version control.

Review the settings in [.env.example](.env.example), especially:

| Variables | Purpose |
| --- | --- |
| `TRIGGER_AUTH_TOKEN`, `STAGE2_AUTH_TOKEN` | Webhook authentication; configure matching secrets for the services that communicate. |
| `CAUSA_API_VIEWER_TOKEN`, `CAUSA_REMEDIATION_PROPOSER_TOKEN`, `REMEDIATION_APPROVAL_TOKEN` | Dashboard and remediation access. Replace the example values. |
| `PROMETHEUS_URL`, `TEMPO_URL`, `LOKI_URL` | Telemetry backend addresses. |
| `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` | PostgreSQL connection; remediation has separate role settings. |
| `CAUSA_APP_NAMESPACE`, `CAUSA_PROFILE` | Monitored workload scope and profile. |
| `CAUSA_NIM_ENABLED`, `NVIDIA_API_KEY` | Optional NIM-assisted remediation; set `CAUSA_NIM_ENABLED=0` to disable it. |
| `CAUSA_REMEDIATION_AUTO_PROPOSE`, `CAUSA_REMEDIATION_AUTO_APPLY` | Automation switches; both default to `0` in the example. |

### Run the local demo

From the repository root, after configuring `.env`:

```bash
./causa-startup.sh
./microservice-startup.sh
```

The first script creates or starts a Kind cluster as needed, deploys the observability stack if absent, installs dashboard dependencies, and starts local processes and port-forwards. It changes the current kubectl context and attempts to stop processes occupying its ports. The dashboard defaults to <http://127.0.0.1:5174>, with the API on port `8090`; logs are written under `.run/logs/`. The second script installs the OpenTelemetry demo. Setting `RECREATE_DEMO_NAMESPACE=1` enables a destructive demo namespace reinstall.

For frontend-only development, once the API is available:

```bash
cd dashboard/frontend
npm ci
npm run dev
```

Vite listens on port `5174` and proxies `/api` to `localhost:8090`.

### Test and build

From the repository root:

```bash
cd incident-detection
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,trigger]'
.venv/bin/pip install -r requirements-devc.txt
PYTHONPATH=. .venv/bin/pytest -q tests

cd ../dashboard/frontend
npm ci
npm run lint
npm test
npm run build
```

Some Python integration tests require PostgreSQL and `CAUSA_TEST_PG_DSN`; without it, those checks may be skipped. The [CI workflow](.github/workflows/eval.yml) sets up PostgreSQL and loads the rendered schema. To check Prometheus rule packs from the repository root, run `bash scripts/check-prom-rules.sh`; see the script for `promtool` requirements.

## Usage examples

Check that the dashboard process is alive after startup:

```bash
curl http://127.0.0.1:8090/health
```

The `/api/health` endpoint also checks dashboard store initialization and PostgreSQL readiness. To run the repository's fixture evaluation from `incident-detection/`:

```bash
PYTHONPATH=. .venv/bin/python -m eval.runner
```

For the remediation proposal demo, CI runs `PYTHONPATH=. python -m remediation.cli --auto-approve` from `incident-detection/`. Review the [remediation documentation](incident-detection/README_DEVC.md) and safety settings before working with real targets.

## Docker and deployment

The repository provides Dockerfiles for the [dashboard](dashboard/Dockerfile), [ingestion service](incident-detection/Dockerfile.ingest), [stage-two RCA](incident-detection/Dockerfile.stage2-rca), and [remediation worker](incident-detection/Dockerfile.remediation-worker). The dashboard build uses the repository root as its build context. Helm charts and deployment resources are under [incident-detection/helm/](incident-detection/helm/), [incidents/](incidents/), and [dashboard/helm/](dashboard/helm/).

Deployment needs configured secrets, persistence, network restrictions, and the readiness gates documented in [docs/production-readiness.md](docs/production-readiness.md). The remediation CD workflow also requires its configured registry and cluster credentials; it skips automatic builds when a service has no Dockerfile build context.

## Roadmap

The documented path toward production includes a versioned labeled incident corpus, disjoint calibration and holdout sets, published evaluation results, and operational drills. See [production readiness](docs/production-readiness.md) for the concrete gates. TODO: A separately maintained feature roadmap is not identified in this repository.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contract, remediation safety, and verification expectations. Please keep changes focused and include the appropriate checks.

## License

TODO: No license file or license declaration was identified in the repository. Confirm the license with the maintainers before assuming reuse terms.
