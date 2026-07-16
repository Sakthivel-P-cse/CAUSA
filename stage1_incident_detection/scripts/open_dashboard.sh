#!/usr/bin/env bash
set -euo pipefail

CONTEXT="kind-gcp-demo-test"
NAMESPACE="causa-dashboard"
SERVICE="causa-dashboard"
LOCAL_PORT="8091"
REMOTE_PORT="8090"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)
      CONTEXT="$2"
      shift 2
      ;;
    --namespace)
      NAMESPACE="$2"
      shift 2
      ;;
    --service)
      SERVICE="$2"
      shift 2
      ;;
    --local-port)
      LOCAL_PORT="$2"
      shift 2
      ;;
    --remote-port)
      REMOTE_PORT="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--context <ctx>] [--namespace <ns>] [--service <name>] [--local-port <port>] [--remote-port <port>]"
      exit 1
      ;;
  esac
done

if ! command -v kubectl >/dev/null 2>&1; then
  echo "ERROR: kubectl is not installed or not in PATH."
  exit 1
fi

if ! kubectl --context "$CONTEXT" cluster-info --request-timeout=5s >/dev/null 2>&1; then
  echo "ERROR: Kubernetes context '$CONTEXT' is not reachable."
  echo "Check: kind get clusters"
  exit 1
fi

echo "Checking dashboard deployment in namespace '$NAMESPACE'..."
kubectl --context "$CONTEXT" -n "$NAMESPACE" get deploy,pods,svc

ENDPOINTS="$(kubectl --context "$CONTEXT" -n "$NAMESPACE" get endpoints "$SERVICE" -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
if [[ -z "$ENDPOINTS" ]]; then
  echo
  echo "ERROR: Service '$SERVICE' has no ready endpoints."
  echo "Recent dashboard logs:"
  kubectl --context "$CONTEXT" -n "$NAMESPACE" logs deploy/"$SERVICE" --tail=120 || true
  exit 1
fi

if command -v lsof >/dev/null 2>&1 && lsof -ti tcp:"$LOCAL_PORT" >/dev/null 2>&1; then
  echo "Port $LOCAL_PORT is already in use."
  echo "Use another local port, for example:"
  echo "  $0 --local-port 8092"
  exit 1
fi

echo
echo "Starting port-forward: http://localhost:${LOCAL_PORT} -> svc/${SERVICE}:${REMOTE_PORT}"
echo "Keep this terminal open while using the dashboard."
echo

exec kubectl --context "$CONTEXT" -n "$NAMESPACE" port-forward svc/"$SERVICE" "${LOCAL_PORT}:${REMOTE_PORT}"