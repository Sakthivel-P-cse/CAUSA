#!/usr/bin/env bash
# ============================================================
# rebuild_and_deploy.sh — Rebuild images and redeploy after
#                          source-code changes.
#
# Rebuilds:
#   traceops/dashboard:latest   — backend bug-fixes + new frontend UI
#   traceops/stage2-rca:latest  — Loki/Tempo/CPU-memory scorer fixes
#
# Then loads them into the kind cluster and rolls the pods.
#
# Usage:
#   ./scripts/rebuild_and_deploy.sh [--context <kube-context>] [--env dev|prod]
#
# Defaults:
#   --context  kind-gcp-demo-test
#   --env      dev
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"      # stage1_incident_detection/
PROJECT_ROOT="$(cd "${ROOT_DIR}/.." && pwd)"    # traceops/

CONTEXT="kind-gcp-demo-test"
KIND_CLUSTER="gcp-demo-test"
ENV="dev"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)      CONTEXT="$2";      shift 2 ;;
    --kind-cluster) KIND_CLUSTER="$2"; shift 2 ;;
    --env)          ENV="$2";          shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "========================================================"
echo "  TraceOps — Rebuild & Redeploy"
echo "  context: ${CONTEXT}   env: ${ENV}"
echo "========================================================"
echo ""

# ── Pre-flight ─────────────────────────────────────────────
for tool in kubectl docker kind node npm; do
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

# ── Step 1: Build React frontend ────────────────────────────
echo "──────────────────────────────────────────────────────"
echo "[1/6] Building React frontend (npm run build)"
echo "──────────────────────────────────────────────────────"
cd "${PROJECT_ROOT}/dashboard/frontend"
npm install --silent
npm run build
echo "✓ Frontend built → dashboard/frontend/dist/"
echo ""

# ── Step 2: Build stage2-rca Docker image ───────────────────
echo "──────────────────────────────────────────────────────"
echo "[2/6] Building Docker image: traceops/stage2-rca:latest"
echo "──────────────────────────────────────────────────────"
cd "${PROJECT_ROOT}"
docker build --no-cache -f Dockerfile.stage2-rca -t traceops/stage2-rca:latest .
echo "✓ traceops/stage2-rca:latest built"
echo ""

# ── Step 3: Build dashboard Docker image ────────────────────
echo "──────────────────────────────────────────────────────"
echo "[3/6] Building Docker image: traceops/dashboard:latest"
echo "──────────────────────────────────────────────────────"
docker build -f Dockerfile.dashboard -t traceops/dashboard:latest .
echo "✓ traceops/dashboard:latest built"
echo ""

# ── Step 4: Load images into kind cluster ───────────────────
echo "──────────────────────────────────────────────────────"
echo "[4/6] Loading images into kind cluster '${KIND_CLUSTER}'"
echo "──────────────────────────────────────────────────────"
kind load docker-image traceops/stage2-rca:latest  --name "${KIND_CLUSTER}"
kind load docker-image traceops/dashboard:latest   --name "${KIND_CLUSTER}"
echo "✓ Images loaded"
echo ""

# ── Step 5: Rolling restart of pods ─────────────────────────
echo "──────────────────────────────────────────────────────"
echo "[5/6] Rolling restart: stage2-rca"
echo "──────────────────────────────────────────────────────"
helm upgrade --install stage2-rca "${ROOT_DIR}/helm/stage2-rca" \
  --kube-context "${CONTEXT}" \
  --namespace stage2-rca \
  --create-namespace \
  -f "${ROOT_DIR}/helm/stage2-rca/values.yaml" \
  -f "${ROOT_DIR}/helm/stage2-rca/values-${ENV}.yaml" \
  --wait \
  --timeout 3m
echo "✓ stage2-rca restarted"
echo ""

echo "──────────────────────────────────────────────────────"
echo "[6/6] Rolling restart: traceops-dashboard"
echo "──────────────────────────────────────────────────────"
helm upgrade --install traceops-dashboard "${ROOT_DIR}/helm/traceops-dashboard" \
  --kube-context "${CONTEXT}" \
  --namespace traceops-dashboard \
  --create-namespace \
  -f "${ROOT_DIR}/helm/traceops-dashboard/values.yaml" \
  -f "${ROOT_DIR}/helm/traceops-dashboard/values-${ENV}.yaml" \
  --wait \
  --timeout 3m
echo "✓ traceops-dashboard restarted"
echo ""

# ── Done ────────────────────────────────────────────────────
echo "========================================================"
echo "  All images rebuilt and pods restarted successfully!"
echo "========================================================"
echo ""
echo "  Access the dashboard:"
echo "    kubectl --context ${CONTEXT} -n traceops-dashboard \\"
echo "      port-forward svc/traceops-dashboard 8090:8090"
echo "    → http://localhost:8090"
echo ""
echo "  Verify:"
echo "    curl -s http://localhost:8090/health"
echo ""
