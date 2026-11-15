#!/usr/bin/env bash
# boutique_smoke.sh — held-out A+B smoke (NOT weight tuning).
# Assumes Online Boutique is installed and patch_gcp_demo_telemetry.sh applied.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT="${CONTEXT:-kind-gcp-demo-test}"
APP_NS="${APP_NS:-gcp-demo}"

echo "Boutique smoke (held-out) — context=${CONTEXT} ns=${APP_NS}"

if ! kubectl --context "${CONTEXT}" -n causa-ingest get deploy causa-ingest &>/dev/null; then
  echo "ERROR: causa-ingest not deployed"
  exit 1
fi

kubectl --context "${CONTEXT}" -n causa-ingest rollout status deploy/causa-ingest --timeout=60s

PF=""
cleanup() { [[ -n "${PF}" ]] && kill "${PF}" 2>/dev/null || true; }
trap cleanup EXIT
kubectl --context "${CONTEXT}" -n causa-ingest port-forward svc/causa-ingest 18080:8080 &>/dev/null &
PF=$!
sleep 2

curl -sf http://127.0.0.1:18080/health >/dev/null
echo "  ingest /health OK"

curl -sf -X POST http://127.0.0.1:18080/trigger \
  -H 'Content-Type: application/json' \
  -d "{\"status\":\"firing\",\"alerts\":[{\"status\":\"firing\",\"labels\":{\"alertname\":\"BoutiqueSmoke\",\"service\":\"frontend\",\"namespace\":\"${APP_NS}\",\"severity\":\"warning\"},\"startsAt\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"fingerprint\":\"boutique-smoke-1\"}]}" \
  | grep -q accepted

echo "  synthetic alert accepted (202)"
echo "Boutique smoke complete (no ranking claims)"
