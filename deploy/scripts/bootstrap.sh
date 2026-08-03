#!/usr/bin/env bash
# bootstrap.sh — One path that makes A+B+C actually work on kind.
#
# Usage:
#   ./scripts/bootstrap.sh [--context <ctx>] [--kind-cluster <name>] [--env dev|prod]
#     [--skip-deploy] [--catalog] [--chaos S5] [--smoke-loop] [--with-dashboard]
#     [--baseline-minutes N]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

CONTEXT="kind-gcp-demo-test"
KIND_CLUSTER="gcp-demo-test"
ENV="dev"
SKIP_DEPLOY=0
RUN_CATALOG=0
CHAOS_ID=""
SMOKE_LOOP=0
WITH_DASHBOARD=0
BASELINE_MINUTES=15
APP_NS="otel-demo"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context) CONTEXT="$2"; shift 2 ;;
    --kind-cluster) KIND_CLUSTER="$2"; shift 2 ;;
    --env) ENV="$2"; shift 2 ;;
    --skip-deploy) SKIP_DEPLOY=1; shift ;;
    --catalog) RUN_CATALOG=1; shift ;;
    --chaos) CHAOS_ID="$2"; shift 2 ;;
    --smoke-loop) SMOKE_LOOP=1; shift ;;
    --with-dashboard) WITH_DASHBOARD=1; shift ;;
    --baseline-minutes) BASELINE_MINUTES="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "========================================================"
echo "  CAUSA bootstrap (A+B+C)"
echo "  context=${CONTEXT} cluster=${KIND_CLUSTER} env=${ENV}"
echo "========================================================"

if ! kubectl --context "${CONTEXT}" cluster-info --request-timeout=5s &>/dev/null; then
  echo "ERROR: cannot reach context ${CONTEXT}."
  echo "Create kind first, e.g.: kind create cluster --name ${KIND_CLUSTER}"
  exit 1
fi

echo "[1/10] Building + loading images"
"${SCRIPT_DIR}/build_images.sh" --kind-cluster "${KIND_CLUSTER}"

if [[ "${SKIP_DEPLOY}" -eq 0 ]]; then
  echo "[2/10] Deploying stack"
  "${SCRIPT_DIR}/deploy.sh" "${ENV}" --context "${CONTEXT}"
else
  echo "[2/10] Skipping deploy (--skip-deploy)"
fi

if [[ "${WITH_DASHBOARD}" -eq 1 ]]; then
  echo "[2b] Deploying causa-dashboard"
  helm upgrade --install causa-dashboard "${ROOT_DIR}/dashboard/helm/causa-dashboard" \
    --kube-context "${CONTEXT}" \
    --namespace causa-dashboard \
    --create-namespace \
    -f "${ROOT_DIR}/dashboard/helm/causa-dashboard/values.yaml" \
    --wait --timeout 3m || true
fi

echo "[3/10] Waiting for core Deployments"
for pair in \
  "postgres/postgres" \
  "causa-ingest/causa-ingest" \
  "stage2-rca/stage2-rca" \
  "prometheus/prometheus" \
  "alertmanager/alertmanager"; do
  ns="${pair%%/*}"
  dep="${pair##*/}"
  kubectl --context "${CONTEXT}" -n "${ns}" rollout status "deployment/${dep}" --timeout=180s
done

echo "[4/10] Proving schema (uq_rca_incident_id, anomaly_baseline, stage2_outbox)"
IDX=$(kubectl --context "${CONTEXT}" -n postgres exec deployment/postgres -- \
  psql -U causa -d causa -tAc \
  "SELECT COUNT(*) FROM pg_indexes WHERE indexname='uq_rca_incident_id';" | tr -d '[:space:]')
[[ "${IDX}" == "1" ]] || { echo "ERROR: uq_rca_incident_id missing"; exit 1; }
for tbl in anomaly_baseline stage2_outbox incidents rca_reports; do
  n=$(kubectl --context "${CONTEXT}" -n postgres exec deployment/postgres -- \
    psql -U causa -d causa -tAc \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='${tbl}';" | tr -d '[:space:]')
  [[ "${n}" == "1" ]] || { echo "ERROR: table ${tbl} missing"; exit 1; }
done
echo "  schema OK"

echo "[5/10] Capture baselines → Postgres"
PF_PID=""
cleanup() { [[ -n "${PF_PID}" ]] && kill "${PF_PID}" 2>/dev/null || true; }
trap cleanup EXIT
kubectl --context "${CONTEXT}" -n postgres port-forward svc/postgres 15432:5432 &>/dev/null &
PF_PID=$!
sleep 2
export PGHOST=127.0.0.1 PGPORT=15432 PGDATABASE=causa PGUSER=causa
export PGPASSWORD="${PGPASSWORD:-causa}"
# Prefer in-cluster URLs via additional port-forwards when available; capture
# may still work if TelemetryAdapter defaults resolve via kube DNS from a job.
# Local bootstrap uses host port-forwards for Prom when present.
PROM_PF=""
kubectl --context "${CONTEXT}" -n prometheus port-forward svc/prometheus 19090:9090 &>/dev/null &
PROM_PF=$!
sleep 2
mkdir -p "${ROOT_DIR}/baselines"
cd "${ROOT_DIR}"
python -m testbed.harness.capture_baseline \
  --namespace "${APP_NS}" \
  --window-minutes "${BASELINE_MINUTES}" \
  --out "${ROOT_DIR}/baselines/${APP_NS}.json" \
  --write-db \
  --prometheus-url "http://127.0.0.1:19090" \
  || echo "WARN: capture_baseline failed (demo may not be installed yet)"
kill "${PROM_PF}" 2>/dev/null || true

BL_COUNT=$(kubectl --context "${CONTEXT}" -n postgres exec deployment/postgres -- \
  psql -U causa -d causa -tAc "SELECT COUNT(*) FROM anomaly_baseline;" | tr -d '[:space:]')
echo "  anomaly_baseline rows: ${BL_COUNT}"
if [[ "${BL_COUNT}" == "0" ]]; then
  echo "WARN: anomaly_baseline empty — Stage-2 will abstain missing_anomaly_baselines"
fi

echo "[6/10] validate.sh --profile rca"
"${SCRIPT_DIR}/validate.sh" --context "${CONTEXT}" --profile rca || true

echo "[7/10] Infra handoff tests (env-gated)"
export CAUSA_TEST_PG_DSN="host=127.0.0.1 port=15432 dbname=causa user=causa password=${PGPASSWORD}"
export PROMETHEUS_URL="http://127.0.0.1:19090"
kubectl --context "${CONTEXT}" -n prometheus port-forward svc/prometheus 19090:9090 &>/dev/null &
PROM_PF=$!
sleep 2
NODE=$(kubectl --context "${CONTEXT}" get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
export CAUSA_TEST_NODE="${NODE}"
cd "${ROOT_DIR}"
python -m pytest tests/test_infra_handoff.py -q --tb=line || true
kill "${PROM_PF}" 2>/dev/null || true

if [[ -n "${CHAOS_ID}" ]]; then
  echo "[8/10] Chaos ${CHAOS_ID}"
  python -m testbed.harness.run_scenario --scenario "${CHAOS_ID}" --write-db || true
fi

if [[ "${RUN_CATALOG}" -eq 1 ]]; then
  echo "[9/10] Catalog S0–S5 D1–D3"
  for sid in S0 S1 S2 S3 S4 S5 D1 D2 D3; do
    python -m testbed.harness.run_scenario --scenario "${sid}" --write-db || true
  done
  echo "  C live eval"
  export CAUSA_EVAL_DSN="host=127.0.0.1 port=15432 dbname=causa user=causa_eval password=causa_eval"
  python -m eval.live_runner --dsn "${CAUSA_EVAL_DSN}" \
    --out "${ROOT_DIR}/eval/artifacts/live_eval.json" || true
  python -m eval.fit_from_live --dsn "${CAUSA_EVAL_DSN}" \
    --out "${ROOT_DIR}/eval/artifacts/fit_result.json" || true
fi

if [[ "${SMOKE_LOOP}" -eq 1 ]]; then
  echo "[10/10] Scale-zero loop smoke (NOT accuracy)"
  "${SCRIPT_DIR}/e2e_all_services_rca.sh" || true
fi

echo "========================================================"
echo "  Bootstrap finished"
echo "========================================================"
