#!/usr/bin/env bash
# ============================================================
# cleanup.sh — Remove all Causa stack resources
#
# Usage:
#   ./scripts/cleanup.sh [--force]
#
# --force: skip confirmation prompt
# ============================================================
set -euo pipefail

FORCE="${1:-}"

RELEASES=("causa-dashboard" "stage2-rca" "postgres" "alertmanager" "otel-agent" "otel-lb" "otel-collector" "prometheus" "mimir" "minio-mimir" "loki" "minio-loki" "tempo" "minio-tempo" "node-exporter" "kube-state-metrics")
NAMESPACES=("causa-dashboard" "stage2-rca" "postgres" "alertmanager" "otel-agent" "otel-lb" "otel-collector" "prometheus" "mimir" "minio-mimir" "loki" "minio-loki" "tempo" "minio-tempo" "node-exporter" "kube-state-metrics")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "========================================================"
echo "  Causa Observability Stack — CLEANUP"
echo "========================================================"
echo ""
echo "  This will remove:"
for r in "${RELEASES[@]}"; do
  echo "    - Helm release: ${r}"
done
for ns in "${NAMESPACES[@]}"; do
  echo "    - Namespace:    ${ns}"
done
echo ""

if [[ "${FORCE}" != "--force" ]]; then
  read -rp "  Proceed? [y/N] " confirm
  if [[ "${confirm}" != "y" && "${confirm}" != "Y" ]]; then
    echo "Aborted."
    exit 0
  fi
fi

echo ""

# Uninstall Helm releases (reverse order of deployment)
for release in "${RELEASES[@]}"; do
  # Determine namespace (same name as release for this stack)
  ns="${release}"
  echo "  Uninstalling Helm release: ${release} (namespace: ${ns})"
  if helm status "${release}" --namespace "${ns}" &>/dev/null; then
    helm uninstall "${release}" --namespace "${ns}" || true
    echo "  ✓ ${release} uninstalled"
  else
    echo "  ⚠  ${release} not found — skipping"
  fi
done

echo ""

# Delete namespaces
for ns in "${NAMESPACES[@]}"; do
  echo "  Deleting namespace: ${ns}"
  kubectl delete namespace "${ns}" --ignore-not-found
done

# Remove cluster-level RBAC created by Prometheus and kube-state-metrics charts
echo ""
echo "  Cleaning up cluster-level RBAC..."
kubectl delete clusterrole prometheus --ignore-not-found
kubectl delete clusterrolebinding prometheus --ignore-not-found
kubectl delete clusterrole kube-state-metrics --ignore-not-found
kubectl delete clusterrolebinding kube-state-metrics --ignore-not-found

echo ""
echo "========================================================"
echo "  Cleanup complete."
echo "========================================================"
