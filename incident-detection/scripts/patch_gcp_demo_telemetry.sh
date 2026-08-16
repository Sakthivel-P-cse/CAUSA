#!/usr/bin/env bash
# ============================================================
# patch_gcp_demo_telemetry.sh
#
# Enable OpenTelemetry trace export for Google Online Boutique
# services so they emit through the Causa OTel stack.
#
# PRD2 §12 topology (agent DaemonSet → LB → gateway):
#   apps → node-local OTel Agent :4317 → otel-lb (traces) /
#          otel-collector (metrics/logs) → Tempo / Prom / Loki
#
# Default: send to the otel-agent Service (agent tier, not gateway).
# Optional --node-local: inject status.hostIP so each pod hits the
# agent on its own node (preferred for production-shaped demos).
#
# Usage:
#   ./scripts/patch_gcp_demo_telemetry.sh \
#     [--context kind-gcp-demo-test] \
#     [--namespace gcp-demo] \
#     [--collector otel-agent.otel-agent.svc.cluster.local:4317] \
#     [--node-local] \
#     [--no-wait]
#
# Notes:
# - This patches runtime Deployment env vars only.
# - Re-run after redeploying Online Boutique from the default
#   release manifest (tracing is off by default).
# - Do NOT point --collector at otel-collector (gateway): that
#   bypasses k8sattributes enrichment and the trace-ID LB path.
# ============================================================
set -euo pipefail

KUBE_CONTEXT=""
NAMESPACE="gcp-demo"
# Default: agent Service — keeps apps on the agent → LB → gateway path.
COLLECTOR_ADDR="otel-agent.otel-agent.svc.cluster.local:4317"
NODE_LOCAL=0
WAIT_ROLLOUT=1

SERVICES=(
  frontend
  checkoutservice
  currencyservice
  emailservice
  paymentservice
  productcatalogservice
  recommendationservice
  shippingservice
)

usage() {
  cat <<'EOF'
Usage:
  ./scripts/patch_gcp_demo_telemetry.sh [options]

Options:
  --context <name>      Kubernetes context to use (optional)
  --namespace <name>    Namespace where Online Boutique runs (default: gcp-demo)
  --collector <addr>    Agent host:port (default: otel-agent.otel-agent.svc.cluster.local:4317)
                        Prefer the agent tier. Avoid otel-collector (gateway) addresses.
  --node-local          Inject HOST_IP (status.hostIP) and set
                        COLLECTOR_SERVICE_ADDR=$(HOST_IP):4317 per pod
  --no-wait             Do not wait for deployment rollout after patching
  -h, --help            Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)
      KUBE_CONTEXT="${2:-}"
      shift 2
      ;;
    --namespace)
      NAMESPACE="${2:-}"
      shift 2
      ;;
    --collector)
      COLLECTOR_ADDR="${2:-}"
      shift 2
      ;;
    --node-local)
      NODE_LOCAL=1
      shift
      ;;
    --no-wait)
      WAIT_ROLLOUT=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

# Guard: refuse gateway address unless the user overrides after a warning.
if [[ "${COLLECTOR_ADDR}" == *"otel-collector"* ]] && [[ "${NODE_LOCAL}" -eq 0 ]]; then
  echo "WARNING: COLLECTOR_ADDR points at the gateway (${COLLECTOR_ADDR})."
  echo "  PRD2 §12 expects apps → agent DaemonSet, not the gateway."
  echo "  Prefer: otel-agent.otel-agent.svc.cluster.local:4317  or  --node-local"
  echo ""
fi

KCTL=(kubectl)
if [[ -n "${KUBE_CONTEXT}" ]]; then
  KCTL=(kubectl --context "${KUBE_CONTEXT}")
fi

echo "========================================================"
echo "  Patch Online Boutique Telemetry"
echo "========================================================"
echo "  Namespace: ${NAMESPACE}"
if [[ -n "${KUBE_CONTEXT}" ]]; then
  echo "  Context:   ${KUBE_CONTEXT}"
else
  echo "  Context:   $("${KCTL[@]}" config current-context)"
fi
if [[ "${NODE_LOCAL}" -eq 1 ]]; then
  echo "  Collector: \$(HOST_IP):4317  (node-local agent via downward API)"
else
  echo "  Collector: ${COLLECTOR_ADDR}  (agent tier)"
fi
echo ""

patch_node_local() {
  local svc="$1"
  # Strategic merge: inject HOST_IP + COLLECTOR_SERVICE_ADDR=$(HOST_IP):4317
  # Kubernetes expands $(HOST_IP) from the sibling env var.
  "${KCTL[@]}" -n "${NAMESPACE}" patch "deployment/${svc}" --type='strategic' -p "{
    \"spec\": {
      \"template\": {
        \"spec\": {
          \"containers\": [{
            \"name\": \"${svc}\",
            \"env\": [
              {
                \"name\": \"HOST_IP\",
                \"valueFrom\": {
                  \"fieldRef\": {
                    \"fieldPath\": \"status.hostIP\"
                  }
                }
              },
              {
                \"name\": \"COLLECTOR_SERVICE_ADDR\",
                \"value\": \"\$(HOST_IP):4317\"
              },
              {
                \"name\": \"OTEL_EXPORTER_OTLP_ENDPOINT\",
                \"value\": \"http://\$(HOST_IP):4317\"
              },
              {
                \"name\": \"ENABLE_TRACING\",
                \"value\": \"1\"
              },
              {
                \"name\": \"OTEL_SERVICE_NAME\",
                \"value\": \"${svc}\"
              }
            ]
          }]
        }
      }
    }
  }" >/dev/null
}

patch_agent_service() {
  local svc="$1"
  "${KCTL[@]}" -n "${NAMESPACE}" set env "deployment/${svc}" \
    "COLLECTOR_SERVICE_ADDR=${COLLECTOR_ADDR}" \
    "OTEL_EXPORTER_OTLP_ENDPOINT=http://${COLLECTOR_ADDR}" \
    "ENABLE_TRACING=1" \
    "OTEL_SERVICE_NAME=${svc}" >/dev/null
}

for svc in "${SERVICES[@]}"; do
  if ! "${KCTL[@]}" -n "${NAMESPACE}" get deploy "${svc}" >/dev/null 2>&1; then
    echo "  - SKIP ${svc} (deployment not found)"
    continue
  fi

  echo "  - Patching ${svc}"
  if [[ "${NODE_LOCAL}" -eq 1 ]]; then
    # Some Boutique images name the container differently; try service name first.
    if ! patch_node_local "${svc}" 2>/dev/null; then
      # Fall back: patch all containers via set env + a small HOST_IP JSON patch
      echo "    (strategic patch by container name failed — using agent Service fallback)"
      patch_agent_service "${svc}"
    fi
  else
    patch_agent_service "${svc}"
  fi

  if [[ "${WAIT_ROLLOUT}" -eq 1 ]]; then
    "${KCTL[@]}" -n "${NAMESPACE}" rollout status "deployment/${svc}" --timeout=240s >/dev/null
    echo "    OK ${svc} rollout completed"
  else
    echo "    OK ${svc} patched"
  fi
done

echo ""
echo "Verification snapshot:"
for svc in "${SERVICES[@]}"; do
  if ! "${KCTL[@]}" -n "${NAMESPACE}" get deploy "${svc}" >/dev/null 2>&1; then
    continue
  fi
  echo "  --- ${svc} ---"
  "${KCTL[@]}" -n "${NAMESPACE}" get deploy "${svc}" -o jsonpath='{range .spec.template.spec.containers[*].env[*]}{.name}={.value}{"\n"}{end}' \
    | grep -E 'COLLECTOR_SERVICE_ADDR|OTEL_EXPORTER_OTLP_ENDPOINT|ENABLE_TRACING|OTEL_SERVICE_NAME|HOST_IP' || true
done

echo ""
echo "Done."
echo "Next checks:"
echo "  1) Confirm app traces in Tempo (service=frontend, checkoutservice, etc.)"
echo "  2) Confirm service graph metrics in Prometheus (traces_service_graph_request_total)"
echo "  3) Confirm spans carry k8s.* attributes (enriched by otel-agent)"
