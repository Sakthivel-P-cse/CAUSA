#!/usr/bin/env bash
# ============================================================
# apply_rca_fixes.sh — Apply Stage 2 RCA bug fixes
#
# Fixes applied:
#   1. Rebuilds stage2-rca image with smarter alerted_service
#      extraction in webhook_receiver.py
#   2. Reloads Prometheus alert rules ConfigMap with:
#      - ServiceHighErrorRate / ServiceHighLatency now use
#        traces_service_graph_request_* (metrics that exist)
#      - PodCrashLooping, DeploymentReplicasUnavailable, and
#        ContainerMemoryNearLimit now expose a ``service`` label
#        via label_replace so Stage 2 can identify the service
#
# Usage:
#   ./scripts/apply_rca_fixes.sh [--context <kube-context>] [--env dev|prod]
#
# Defaults:
#   --context  kind-gcp-demo-test
#   --env      dev
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
HELM_DIR="${ROOT_DIR}/observability/helm"

CONTEXT="kind-gcp-demo-test"
ENV="dev"
KIND_CLUSTER="gcp-demo-test"   # kind cluster name (without the "kind-" prefix)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context) CONTEXT="$2";  shift 2 ;;
    --env)     ENV="$2";       shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

VALUES_FILE="values-${ENV}.yaml"

echo "========================================================"
echo "  Causa RCA Fixes — context: ${CONTEXT}  env: ${ENV}"
echo "========================================================"
echo ""

# ── Pre-flight ─────────────────────────────────────────────
for tool in kubectl helm docker kind; do
  if ! command -v "${tool}" &>/dev/null; then
    echo "ERROR: '${tool}' not found in PATH."
    exit 1
  fi
done

if ! kubectl --context "${CONTEXT}" cluster-info --request-timeout=5s &>/dev/null; then
  echo "ERROR: Cannot reach cluster with context '${CONTEXT}'."
  exit 1
fi
echo "✓ Cluster reachable"
echo ""

# ── Step 1–2: Rebuild + load ingest and stage2 images ─────
echo "──────────────────────────────────────────────────────"
echo "[1/3] Building + loading causa/ingest and causa/stage2-rca"
echo "──────────────────────────────────────────────────────"
"${SCRIPT_DIR}/build_images.sh" --kind-cluster "${KIND_CLUSTER}"
echo ""

# ── Step 3: Reload Prometheus alert rules via helm upgrade ─
echo "──────────────────────────────────────────────────────"
echo "[2/3] Upgrading Prometheus helm release (reload alert rules)"
echo "──────────────────────────────────────────────────────"
helm upgrade --install prometheus "${HELM_DIR}/prometheus" \
  --kube-context "${CONTEXT}" \
  --namespace prometheus \
  --create-namespace \
  -f "${HELM_DIR}/prometheus/values.yaml" \
  -f "${HELM_DIR}/prometheus/${VALUES_FILE}" \
  --wait \
  --timeout 3m
echo "✓ Prometheus rules reloaded"
echo ""

# ── Step 4: Restart stage2-rca to pick up the new image ───
echo "──────────────────────────────────────────────────────"
echo "[3/3] Rolling restart of stage2-rca + causa-ingest"
echo "──────────────────────────────────────────────────────"
kubectl --context "${CONTEXT}" \
  -n stage2-rca \
  rollout restart deployment/stage2-rca
kubectl --context "${CONTEXT}" \
  -n stage2-rca \
  rollout status deployment/stage2-rca --timeout=120s
echo "✓ stage2-rca restarted"
if kubectl --context "${CONTEXT}" -n causa-ingest get deployment causa-ingest &>/dev/null; then
  kubectl --context "${CONTEXT}" \
    -n causa-ingest \
    rollout restart deployment/causa-ingest
  kubectl --context "${CONTEXT}" \
    -n causa-ingest \
    rollout status deployment/causa-ingest --timeout=120s
  echo "✓ causa-ingest restarted"
fi
echo ""

# ── Verification ───────────────────────────────────────────
echo "──────────────────────────────────────────────────────"
echo "Verifying fixes..."
echo "──────────────────────────────────────────────────────"

# Check stage2-rca health
RCA_POD=$(kubectl --context "${CONTEXT}" -n stage2-rca get pod \
  -l app=stage2-rca -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [[ -z "${RCA_POD}" ]]; then
  echo "WARN: No stage2-rca pod found — check deployment"
else
  HEALTH=$(kubectl --context "${CONTEXT}" -n stage2-rca exec "${RCA_POD}" \
    -- python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/health').read().decode())" 2>/dev/null || echo "unreachable")
  echo "  stage2-rca /health: ${HEALTH}"
fi

# Check Prometheus loaded the new rules
PROM_POD=$(kubectl --context "${CONTEXT}" -n prometheus get pod \
  -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || \
  kubectl --context "${CONTEXT}" -n prometheus get pod \
  -l app=prometheus -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [[ -n "${PROM_POD}" ]]; then
  RULE_COUNT=$(kubectl --context "${CONTEXT}" -n prometheus exec "${PROM_POD}" \
    -- sh -c "wget -qO- http://localhost:9090/api/v1/rules 2>/dev/null | grep -o 'ServiceHighErrorRate\|DeploymentReplicasUnavailable\|PodCrashLooping' | wc -l" 2>/dev/null || echo "?")
  echo "  Prometheus key rule matches: ${RULE_COUNT} (expect ≥ 3)"
fi

echo ""
echo "========================================================"
echo "  All fixes applied successfully!"
echo ""
echo "  What changed:"
echo "    webhook_receiver.py  — alerted_service now checks:"
echo "      service > server > deployment > container > pod > instance > namespace"
echo ""
echo "    alert rules          — ServiceHighErrorRate and ServiceHighLatency"
echo "      now use traces_service_graph_request_* (Tempo metrics that exist)"
echo ""
echo "    alert rules          — DeploymentReplicasUnavailable, PodCrashLooping,"
echo "      and ContainerMemoryNearLimit now carry a 'service' label via"
echo "      label_replace so Stage 2 can identify the real service."
echo "========================================================"
