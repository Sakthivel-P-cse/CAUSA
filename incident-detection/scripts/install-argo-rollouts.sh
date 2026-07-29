#!/usr/bin/env bash
# Install Argo Rollouts + AnalysisTemplate and migrate recommendation to a Rollout.
set -euo pipefail
CONTEXT="${CONTEXT:-kind-gcp-demo-test}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS_ARGO="${ARGO_NAMESPACE:-argo-rollouts}"
NS_DEMO="${DEMO_NS:-otel-demo}"

log() { printf '==> %s\n' "$*"; }

kubectl --context "${CONTEXT}" create namespace "${NS_ARGO}" --dry-run=client -o yaml | kubectl --context "${CONTEXT}" apply -f -
log "Installing Argo Rollouts controller"
kubectl --context "${CONTEXT}" apply -n "${NS_ARGO}" -f https://github.com/argoproj/argo-rollouts/releases/download/v1.7.2/install.yaml
kubectl --context "${CONTEXT}" -n "${NS_ARGO}" rollout status deploy/argo-rollouts --timeout=180s || true

log "Applying AnalysisTemplate"
kubectl --context "${CONTEXT}" apply -f "${ROOT}/incident-detection/k8s/argo/analysis-template.yaml"

# Capture current recommendation image if deployment exists
IMG=""
if kubectl --context "${CONTEXT}" -n "${NS_DEMO}" get deploy recommendation &>/dev/null; then
  IMG="$(kubectl --context "${CONTEXT}" -n "${NS_DEMO}" get deploy recommendation -o jsonpath='{.spec.template.spec.containers[0].image}')"
  log "Scaling down Deployment/recommendation (image=${IMG})"
  kubectl --context "${CONTEXT}" -n "${NS_DEMO}" scale deploy/recommendation --replicas=0 || true
fi

log "Applying recommendation Rollout"
if [[ -n "${IMG}" ]]; then
  kubectl --context "${CONTEXT}" -n "${NS_DEMO}" apply -f <(
    sed "s|image: .*recommendation|image: ${IMG}|" "${ROOT}/incident-detection/k8s/argo/recommendation-rollout.yaml"
  )
else
  kubectl --context "${CONTEXT}" apply -f "${ROOT}/incident-detection/k8s/argo/recommendation-rollout.yaml"
fi

log "Argo Rollouts ready in ${NS_ARGO}; recommendation Rollout in ${NS_DEMO}"
