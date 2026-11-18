# Phase-2 rule packs and Helm lint

## Phase-2 rule pack check

- Working directory: `incident-detection/` (as in the workflow job default).
- Command: `REQUIRE_PROMTOOL=1 bash ../scripts/check-prom-rules.sh`
- Exit status: `1`.
- Result: chart render and rule-pack content checks passed; required `promtool` syntax check could not run because Docker cannot access its socket.
- Environment: `helm` is v3.20.0+gb2e4314; `/usr/bin/docker` exists; `promtool` is not installed on PATH. The workflow script therefore uses the `prom/prometheus:v3.5.0` Docker image.

Output:

```text
==> helm template (phase2 enabled)
ok causa-alerts-phase1.yml (20 alerts, 0 records)
ok causa-dependencies.yml (8 alerts, 0 records)
ok causa-platform.yml (12 alerts, 0 records)
ok causa-slo-burn.yml (4 alerts, 0 records)
ok causa-slo-recording.yml (0 alerts, 16 records)
rule pack content checks passed
==> promtool check rules (container)
permission denied while trying to connect to the docker API at unix:///var/run/docker.sock
```

## Helm lint loop

- Working directory: repository root, matching the workflow step.
- Command:

```bash
for chart in incident-detection/helm/* dashboard/helm/*; do
  [ -d "$chart" ] || continue
  if [ -f "$chart/Chart.yaml" ]; then
    helm lint "$chart"
  else
    echo "::notice::skipping $chart (no Chart.yaml — raw manifests)"
  fi
done
```

- Exit status: `0`.
- Result: 20 charts reported `1 chart(s) linted, 0 chart(s) failed`. `incident-detection/helm/gatekeeper-remediation` was skipped as raw manifests (no `Chart.yaml`). Helm emitted icon recommendations and the two `[INFO]` messages shown below; these did not fail the loop.

Output:

```text
==> Linting incident-detection/helm/alertmanager
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
engine.go:227: [INFO] Fail: existingSecret is required and must contain dashboard database credentials
==> Linting incident-detection/helm/causa-dashboard
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/causa-ingest
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/causa-remediation-worker
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
::notice::skipping incident-detection/helm/gatekeeper-remediation (no Chart.yaml — raw manifests)
==> Linting incident-detection/helm/kube-state-metrics
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/loki
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/mimir
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/minio-loki
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/minio-mimir
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/minio-tempo
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/node-exporter
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/otel-agent
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/otel-collector
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/otel-lb
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/postgres
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/prometheus
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/stage2-mock
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
engine.go:206: [INFO] Missing required value: env.PGPASSWORD is required when existingSecret is empty
==> Linting incident-detection/helm/stage2-rca
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting incident-detection/helm/tempo
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
==> Linting dashboard/helm/causa-product-dashboard
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
```
