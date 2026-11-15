#!/usr/bin/env bash
# microservice-startup.sh — Start Kind (if needed) + OpenTelemetry Demo apps.
# Prereq for telemetry: Causa stack agent should already be deployed
# (run ./causa-startup.sh once, or ./incident-detection/scripts/deploy.sh dev).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ID_ROOT="${ROOT}/incident-detection"
CONTEXT="${CONTEXT:-kind-gcp-demo-test}"
KIND_CLUSTER="${KIND_CLUSTER:-gcp-demo-test}"
DEMO_NS="${DEMO_NS:-${CAUSA_APP_NAMESPACE:-otel-demo}}"
VALUES="${ID_ROOT}/testbed/otel-demo/values.yaml"

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }

ensure_kind() {
  need docker
  need kind
  need kubectl

  if ! kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER}"; then
    log "Creating kind cluster ${KIND_CLUSTER}"
    kind create cluster --name "${KIND_CLUSTER}"
  fi

  local node="gcp-demo-test-control-plane"
  if docker ps -a --format '{{.Names}}' | grep -qx "${node}"; then
    local st
    st="$(docker inspect -f '{{.State.Status}}' "${node}" 2>/dev/null || true)"
    if [[ "${st}" != "running" ]]; then
      log "Starting kind node ${node}"
      docker start "${node}" >/dev/null
    fi
  fi

  kubectl config use-context "${CONTEXT}" >/dev/null
  local i=0
  until kubectl --context "${CONTEXT}" cluster-info --request-timeout=5s &>/dev/null; do
    i=$((i + 1))
    [[ "${i}" -lt 60 ]] || die "kind cluster ${KIND_CLUSTER} not reachable"
    sleep 2
  done
  log "Kind context ${CONTEXT} is up"
}

ensure_agent() {
  if ! kubectl --context "${CONTEXT}" -n otel-agent get ds otel-agent &>/dev/null; then
    die "otel-agent not found. Deploy Causa stack first: ./causa-startup.sh  (or incident-detection/scripts/deploy.sh dev)"
  fi
}

install_demo() {
  need helm
  [[ -f "${VALUES}" ]] || die "missing ${VALUES}"

  local chart_version="${OTEL_DEMO_CHART_VERSION:-0.40.10}"

  log "Ensuring helm repo open-telemetry"
  helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts >/dev/null 2>&1 || true
  helm repo update open-telemetry >/dev/null

  # Namespace deletion is destructive and must be explicitly requested.
  if [[ "${RECREATE_DEMO_NAMESPACE:-0}" == "1" ]] \
    && helm status otel-demo --kube-context "${CONTEXT}" -n "${DEMO_NS}" &>/dev/null; then
    log "Uninstalling existing otel-demo (clean reinstall)"
    helm uninstall otel-demo --kube-context "${CONTEXT}" -n "${DEMO_NS}" --wait --timeout 5m || true
    kubectl --context "${CONTEXT}" delete namespace "${DEMO_NS}" --wait=true --timeout=5m
  fi
  kubectl --context "${CONTEXT}" create namespace "${DEMO_NS}" \
    --dry-run=client -o yaml | kubectl --context "${CONTEXT}" apply -f -

  log "Installing otel-demo ${chart_version} in ${DEMO_NS}"
  # --wait races flagd Service creation on this chart; install then wait for pods.
  helm upgrade --install otel-demo open-telemetry/opentelemetry-demo \
    --kube-context "${CONTEXT}" \
    --namespace "${DEMO_NS}" \
    --version "${chart_version}" \
    -f "${VALUES}" \
    --timeout 15m \
    --wait=false

  log "Waiting for demo Deployments to become available"
  local ready=0
  local i=0
  while [[ "${i}" -lt 90 ]]; do
    ready="$(kubectl --context "${CONTEXT}" -n "${DEMO_NS}" get deploy \
      --no-headers 2>/dev/null | awk '$2 ~ /^[0-9]+\/[0-9]+$/ && $2 !~ /^0\// {c++} END {print c+0}')"
    local total
    total="$(kubectl --context "${CONTEXT}" -n "${DEMO_NS}" get deploy --no-headers 2>/dev/null | wc -l | tr -d ' ')"
    if [[ "${total}" -gt 0 && "${ready}" -ge "${total}" ]]; then
      break
    fi
    i=$((i + 1))
    sleep 5
  done

  log "Demo pods:"
  kubectl --context "${CONTEXT}" -n "${DEMO_NS}" get pods --no-headers | awk '{printf "  %-40s %s\n", $1, $3}'
}

main() {
  echo "========================================================"
  echo "  Microservice startup (OTel Demo)"
  echo "  context=${CONTEXT}  ns=${DEMO_NS}"
  echo "========================================================"
  ensure_kind
  ensure_agent
  install_demo
  echo ""
  echo "Demo services are running in namespace ${DEMO_NS}."
  echo "Next: ./causa-startup.sh   # port-forwards + Watchdog UI"
}

main "$@"
