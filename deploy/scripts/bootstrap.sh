#!/usr/bin/env bash
# bootstrap.sh — Deploy and verify the Causa stack on kind.
#
# Usage:
#   ./deploy/scripts/bootstrap.sh [--context <ctx>] [--kind-cluster <name>] [--env dev|prod]
#     [--skip-deploy] [--with-dashboard]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CONTEXT="kind-gcp-demo-test"
KIND_CLUSTER="gcp-demo-test"
ENV="dev"
SKIP_DEPLOY=0
WITH_DASHBOARD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context) CONTEXT="$2"; shift 2 ;;
    --kind-cluster) KIND_CLUSTER="$2"; shift 2 ;;
    --env) ENV="$2"; shift 2 ;;
    --skip-deploy) SKIP_DEPLOY=1; shift ;;
    --with-dashboard) WITH_DASHBOARD=1; shift ;;
    --catalog|--chaos|--smoke-loop|--baseline-minutes)
      echo "WARN: demo/chaos options removed; ignoring $1"
      shift
      [[ "${1:-}" =~ ^[^-] ]] && shift || true
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "========================================================"
echo "  CAUSA bootstrap"
echo "  context=${CONTEXT} cluster=${KIND_CLUSTER} env=${ENV}"
echo "========================================================"

if ! kubectl --context "${CONTEXT}" cluster-info --request-timeout=5s &>/dev/null; then
  echo "ERROR: cannot reach context ${CONTEXT}."
  echo "Create kind first, e.g.: kind create cluster --name ${KIND_CLUSTER}"
  exit 1
fi

echo "[1/5] Building + loading images"
"${SCRIPT_DIR}/build_images.sh" --kind-cluster "${KIND_CLUSTER}"

if [[ "${SKIP_DEPLOY}" -eq 0 ]]; then
  echo "[2/5] Deploying stack"
  "${SCRIPT_DIR}/deploy.sh" "${ENV}"
else
  echo "[2/5] Skipping deploy (--skip-deploy)"
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

echo "[3/5] Waiting for core Deployments"
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

echo "[4/5] Proving schema (uq_rca_incident_id, anomaly_baseline, stage2_outbox)"
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

echo "[5/5] validate.sh --profile rca"
"${SCRIPT_DIR}/validate.sh" --context "${CONTEXT}" --profile rca || true

echo "========================================================"
echo "  Bootstrap finished (Causa only — no demo app)"
echo "========================================================"
