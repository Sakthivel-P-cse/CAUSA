#!/usr/bin/env bash
# ============================================================
# rebuild_and_deploy.sh — Rebuild and redeploy Stage-2 RCA.
#
# Usage:
#   ./scripts/rebuild_and_deploy.sh \
#     [--context <kube-context>] [--kind-cluster <name>] \
#     [--env dev|prod] [--stage2 real|mock]
#
# The real engine is the default. `--stage2 mock` explicitly installs the
# development receiver without building or loading an RCA image.
# Dev C components are intentionally outside this script.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

CONTEXT="kind-gcp-demo-test"
KIND_CLUSTER="gcp-demo-test"
ENV="dev"
STAGE2_PROFILE="real"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)      CONTEXT="${2:?--context requires a value}";           shift 2 ;;
    --kind-cluster) KIND_CLUSTER="${2:?--kind-cluster requires a value}"; shift 2 ;;
    --env)          ENV="${2:?--env requires dev or prod}";               shift 2 ;;
    --stage2)       STAGE2_PROFILE="${2:?--stage2 requires real or mock}"; shift 2 ;;
    *) echo "ERROR: unknown option '$1'."; exit 1 ;;
  esac
done

if [[ "${ENV}" != "dev" && "${ENV}" != "prod" ]]; then
  echo "ERROR: --env must be 'dev' or 'prod'. Got: '${ENV}'"
  exit 1
fi
if [[ "${STAGE2_PROFILE}" != "real" && "${STAGE2_PROFILE}" != "mock" ]]; then
  echo "ERROR: --stage2 must be 'real' or 'mock'. Got: '${STAGE2_PROFILE}'"
  exit 1
fi
if [[ "${STAGE2_PROFILE}" == "mock" && "${ENV}" != "dev" ]]; then
  echo "ERROR: the Stage-2 mock is an explicit development-only profile."
  exit 1
fi

echo "========================================================"
echo "  Causa — Rebuild & Redeploy Stage-2"
echo "  context: ${CONTEXT}   env: ${ENV}   profile: ${STAGE2_PROFILE}"
echo "========================================================"
echo ""

# ── Pre-flight ─────────────────────────────────────────────
TOOLS=(kubectl helm)
if [[ "${STAGE2_PROFILE}" == "real" ]]; then
  TOOLS+=(docker kind)
fi
for tool in "${TOOLS[@]}"; do
  if ! command -v "${tool}" &>/dev/null; then
    echo "ERROR: '${tool}' not found in PATH."
    exit 1
  fi
done

if ! kubectl --context "${CONTEXT}" cluster-info --request-timeout=5s &>/dev/null; then
  echo "ERROR: Cannot reach cluster '${CONTEXT}'."
  exit 1
fi
echo "✓ Cluster reachable: ${CONTEXT}"
echo ""

if [[ "${STAGE2_PROFILE}" == "real" ]]; then
  echo "──────────────────────────────────────────────────────"
  echo "[1/3] Building Docker images (ingest + stage2-rca)"
  echo "──────────────────────────────────────────────────────"
  "${SCRIPT_DIR}/build_images.sh" --kind-cluster "${KIND_CLUSTER}"
  echo ""
else
  echo "Using explicit development mock; image build and load skipped."
  echo ""
fi

# Retire the legacy mock release name. Both current profiles share the
# `stage2-rca` release, so profile switches are clean Helm upgrades.
if helm --kube-context "${CONTEXT}" status stage2-mock \
  --namespace stage2-rca &>/dev/null; then
  helm --kube-context "${CONTEXT}" uninstall stage2-mock \
    --namespace stage2-rca \
    --wait
fi

if [[ "${STAGE2_PROFILE}" == "real" ]]; then
  CHART_DIR="${ROOT_DIR}/helm/stage2-rca"
else
  CHART_DIR="${ROOT_DIR}/helm/stage2-mock"
fi

if [[ "${STAGE2_PROFILE}" == "real" ]]; then
  echo "──────────────────────────────────────────────────────"
  echo "[3/4] Deploying Stage-2 ${STAGE2_PROFILE} + causa-ingest"
  echo "──────────────────────────────────────────────────────"
  helm upgrade --install stage2-rca "${CHART_DIR}" \
    --kube-context "${CONTEXT}" \
    --namespace stage2-rca \
    --create-namespace \
    -f "${CHART_DIR}/values.yaml" \
    -f "${CHART_DIR}/values-${ENV}.yaml" \
    --wait \
    --timeout 3m
  helm upgrade --install causa-ingest "${ROOT_DIR}/helm/causa-ingest" \
    --kube-context "${CONTEXT}" \
    --namespace causa-ingest \
    --create-namespace \
    -f "${ROOT_DIR}/helm/causa-ingest/values.yaml" \
    -f "${ROOT_DIR}/helm/causa-ingest/values-${ENV}.yaml" \
    --wait \
    --timeout 3m
else
  echo "──────────────────────────────────────────────────────"
  echo "[3/3] Deploying Stage-2 ${STAGE2_PROFILE}"
  echo "──────────────────────────────────────────────────────"
  helm upgrade --install stage2-rca "${CHART_DIR}" \
    --kube-context "${CONTEXT}" \
    --namespace stage2-rca \
    --create-namespace \
    -f "${CHART_DIR}/values.yaml" \
    -f "${CHART_DIR}/values-${ENV}.yaml" \
    --wait \
    --timeout 3m
fi

echo ""
echo "========================================================"
echo "  Stage-2 ${STAGE2_PROFILE} (+ ingest if real) deployed"
echo "========================================================"
echo "  kubectl --context ${CONTEXT} -n stage2-rca \\"
echo "    port-forward svc/stage2-rca 8081:8080"
