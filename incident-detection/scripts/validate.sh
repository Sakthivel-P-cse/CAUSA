#!/usr/bin/env bash
# ============================================================
# validate.sh — End-to-end health check for the Causa stack
#
# Usage:
#   ./scripts/validate.sh [--context <kube-context>]
#
# Checks:
#   1. All pods are Running (all 16 Dev A+B namespaces)
#   2. Tempo health check
#   3. Loki health check
#   4. Prometheus health check
#   5. OTel Collector health check
#   6. Mimir health check + remote_write success
#   7. Send synthetic OTLP trace via OTel Collector
#   8. Verify trace is queryable in Tempo
#   9. Send synthetic log line to Loki + verify queryable
#  10. Verify Tempo service graph metrics exist in Prometheus (Stage 2 readiness)
#  11. Alertmanager health + Prometheus alert rules loaded
#  12. Postgres health + rca_reports table exists
#  13. Stage 2 RCA engine /health
# ============================================================
set -euo pipefail

PASS=0
FAIL=0
CONTEXT=""
PROFILE="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)
      CONTEXT="$2"
      shift 2
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--context <kube-context>] [--profile infra|app|rca|all]"
      exit 1
      ;;
  esac
done

KCTL=(kubectl)
if [[ -n "${CONTEXT}" ]]; then
  KCTL+=(--context "${CONTEXT}")
fi

k() {
  "${KCTL[@]}" "$@"
}

# ── Utilities ──────────────────────────────────────────────
ok()   { echo "  ✓ $*"; ((PASS++)) || true; }
fail() { echo "  ✗ $*"; ((FAIL++)) || true; }
info() { echo "  → $*"; }

port_forward_bg() {
  local ns="${1}" svc="${2}" local_port="${3}" remote_port="${4}"
  k -n "${ns}" port-forward "svc/${svc}" "${local_port}:${remote_port}" &>/dev/null &
  echo $!
}

cleanup_pids=()
stop_port_forwards() {
  for pid in "${cleanup_pids[@]}"; do
    kill "${pid}" 2>/dev/null || true
  done
}
trap stop_port_forwards EXIT

wait_for_port() {
  local port="${1}"
  local max_wait=15
  local count=0
  while ! curl -s --connect-timeout 1 "http://localhost:${port}" &>/dev/null; do
    sleep 1
    ((count++))
    if [[ "${count}" -ge "${max_wait}" ]]; then
      return 1
    fi
  done
  return 0
}

echo "========================================================"
echo "  Causa Stack — Validation"
echo "========================================================"
if [[ -n "${CONTEXT}" ]]; then
  echo "  Kubernetes context: ${CONTEXT}"
fi
echo ""

# ── Pre-flight: cluster connectivity ──────────────────────
if ! k cluster-info --request-timeout=5s &>/dev/null; then
  fail "Cannot reach Kubernetes cluster${CONTEXT:+ (context: ${CONTEXT})}"
  echo ""
  echo "========================================================"
  echo "  Validation Summary"
  echo "========================================================"
  echo "  Passed: ${PASS}"
  echo "  Failed: ${FAIL}"
  echo ""
  echo "  Cluster is unreachable. Fix kubectl context/connectivity and rerun."
  exit 1
fi
ok "Kubernetes cluster reachable"
echo ""

# ── 1. Pod status ──────────────────────────────────────────
if [[ "${PROFILE}" == "all" || "${PROFILE}" == "infra" ]]; then
echo "[1/13] Checking pod status across all namespaces..."
for ns in tempo minio-tempo loki minio-loki prometheus mimir minio-mimir otel-collector otel-lb otel-agent kube-state-metrics node-exporter alertmanager postgres stage2-rca causa-ingest; do
  pods_raw="$(k -n "${ns}" get pods --no-headers 2>/dev/null || true)"
  total="$(printf "%s\n" "${pods_raw}" | awk 'NF { n++ } END { print n+0 }')"
  # Ignore Terminating pods during rollout; they are expected shortly after upgrades.
  not_running="$(printf "%s\n" "${pods_raw}" | awk '$3 != "Running" && $3 != "Completed" && $3 != "Terminating" { c++ } END { print c+0 }')"
  if [[ "${total}" -eq 0 ]]; then
    fail "${ns}: no pods found"
  elif [[ "${not_running}" -gt 0 ]]; then
    fail "${ns}: ${not_running}/${total} pods not Running"
    k -n "${ns}" get pods
  else
    ok "${ns}: all ${total} pod(s) Running"
  fi
done
echo ""

# ── 2. Tempo healthcheck ───────────────────────────────────
echo "[2/13] Checking Tempo..."
pid=$(port_forward_bg tempo tempo 3200 3200)
cleanup_pids+=("${pid}")
sleep 2
if curl --connect-timeout 2 --max-time 5 -sf http://localhost:3200/ready &>/dev/null; then
  ok "Tempo /ready → 200 OK"
else
  fail "Tempo /ready did not respond"
fi
echo ""

# ── 3. Loki healthcheck ────────────────────────────────────
echo "[3/13] Checking Loki..."
pid=$(port_forward_bg loki loki 3100 3100)
cleanup_pids+=("${pid}")
sleep 2
if curl --connect-timeout 2 --max-time 5 -sf http://localhost:3100/ready &>/dev/null; then
  ok "Loki /ready → 200 OK"
else
  fail "Loki /ready did not respond"
fi
echo ""

# ── 4. Prometheus healthcheck ──────────────────────────────
echo "[4/13] Checking Prometheus..."
pid=$(port_forward_bg prometheus prometheus 9090 9090)
cleanup_pids+=("${pid}")
sleep 2
if curl --connect-timeout 2 --max-time 5 -sf http://localhost:9090/-/ready &>/dev/null; then
  ok "Prometheus /-/ready → 200 OK"
else
  fail "Prometheus /-/ready did not respond"
fi
echo ""

# ── 5. OTel Collector healthcheck ─────────────────────────
echo "[5/13] Checking OTel Collector..."
pid=$(port_forward_bg otel-collector otel-collector 13133 13133)
cleanup_pids+=("${pid}")
sleep 2
if curl --connect-timeout 2 --max-time 5 -sf http://localhost:13133 &>/dev/null; then
  ok "OTel Collector health_check → 200 OK"
else
  fail "OTel Collector health_check did not respond"
fi
echo ""

# ── 6. Mimir health check + remote_write success ────────────────────
echo "[6/13] Checking Mimir (long-term metric storage)..."
pid=$(port_forward_bg mimir mimir 8880 8080)
cleanup_pids+=("${pid}")
sleep 2
if curl --connect-timeout 2 --max-time 5 -sf \
    -H 'X-Scope-OrgID: causa' \
    http://localhost:8880/ready &>/dev/null; then
  ok "Mimir /ready → 200 OK"
else
  fail "Mimir /ready did not respond (not deployed yet, or multitenancy header missing)"
fi

# Check Prometheus is successfully remote_writing to Mimir
# prometheus_remote_storage_samples_total increments for each batch sent.
RW_RESP=$(curl --connect-timeout 2 --max-time 5 -sf \
  "http://localhost:9090/api/v1/query?query=prometheus_remote_storage_samples_total" \
  2>/dev/null || true)
if [[ -n "${RW_RESP}" ]] \
    && echo "${RW_RESP}" | grep -q '"status":"success"' \
    && ! echo "${RW_RESP}" | grep -q '"result":\[\]'; then
  ok "prometheus_remote_storage_samples_total is non-zero — remote_write → Mimir active"
else
  fail "prometheus_remote_storage_samples_total not found or zero — check Prometheus remote_write config"
  info "In dev, remote_write is disabled by default (only enabled in prod values)"
fi
echo ""
fi

# ── 7. Send synthetic trace ─────────────────────────────────────────
if [[ "${PROFILE}" == "all" || "${PROFILE}" == "app" ]]; then
echo "[7/13] Sending synthetic OTLP trace to OTel Collector..."
pid=$(port_forward_bg otel-collector otel-collector 4318 4318)
cleanup_pids+=("${pid}")
sleep 2

TRACE_RESPONSE=$(curl --connect-timeout 2 --max-time 10 -sv -X POST http://localhost:4318/v1/traces \
  -H 'Content-Type: application/json' \
  -d '{
    "resourceSpans": [
      {
        "resource": {
          "attributes": [{"key":"service.name","value":{"stringValue":"causa-validate-client"}}]
        },
        "scopeSpans": [{
          "spans": [{
            "traceId": "aabbccddeeff00112233445566778899",
            "spanId": "aabbccddeeff0011",
            "name": "validate-client-span",
            "kind": 3,
            "startTimeUnixNano": "'"$(date +%s%N)"'",
            "endTimeUnixNano": "'"$(( $(date +%s%N) + 3000000 ))"'",
            "attributes": [
              {"key":"peer.service","value":{"stringValue":"causa-validate-server"}}
            ],
            "status": {"code": 1}
          }]
        }]
      },
      {
        "resource": {
          "attributes": [{"key":"service.name","value":{"stringValue":"causa-validate-server"}}]
        },
        "scopeSpans": [{
          "spans": [{
            "traceId": "aabbccddeeff00112233445566778899",
            "spanId": "0011223344556677",
            "parentSpanId": "aabbccddeeff0011",
            "name": "validate-server-span",
            "kind": 2,
            "startTimeUnixNano": "'"$(date +%s%N)"'",
            "endTimeUnixNano": "'"$(( $(date +%s%N) + 2000000 ))"'",
            "status": {"code": 1}
          }]
        }]
      }
    ]
  }' 2>&1)

if echo "${TRACE_RESPONSE}" | grep -q "200\|HTTP/2 200\|HTTP/1.1 200"; then
  ok "Synthetic OTLP trace accepted by OTel Collector (HTTP 200)"
  info "Trace ID: aabbccddeeff00112233445566778899"
else
  fail "Synthetic OTLP trace was not accepted"
  echo "  Response: ${TRACE_RESPONSE}"
fi
echo ""

# ── 7. Verify trace is queryable in Tempo ──────────────────────
echo "[8/13] Verifying trace is queryable in Tempo..."
# Wait up to 90s for Tempo to ingest the trace. This avoids flakes right after upgrades.
max_wait=90
found=0
for i in $(seq 1 "${max_wait}"); do
  TEMPO_RESP=$(curl --connect-timeout 2 --max-time 3 -sf "http://localhost:3200/api/traces/aabbccddeeff00112233445566778899" 2>/dev/null || true)
  # Tempo can return a populated trace without echoing the raw trace ID string.
  if [[ -n "${TEMPO_RESP}" ]] && echo "${TEMPO_RESP}" | grep -q '"scopeSpans"\|"spans"'; then
    found=1
    break
  fi
  # Re-emit once midway in case the first synthetic trace got dropped during warmup.
  if [[ "${i}" -eq 30 ]]; then
    curl --connect-timeout 2 --max-time 10 -s -X POST http://localhost:4318/v1/traces \
      -H 'Content-Type: application/json' \
      -d '{
        "resourceSpans": [
          {
            "resource": {
              "attributes": [{"key":"service.name","value":{"stringValue":"causa-validate-client"}}]
            },
            "scopeSpans": [{
              "spans": [{
                "traceId": "aabbccddeeff00112233445566778899",
                "spanId": "aabbccddeeff0011",
                "name": "validate-client-span",
                "kind": 3,
                "startTimeUnixNano": "'"$(date +%s%N)"'",
                "endTimeUnixNano": "'"$(( $(date +%s%N) + 3000000 ))"'",
                "attributes": [
                  {"key":"peer.service","value":{"stringValue":"causa-validate-server"}}
                ],
                "status": {"code": 1}
              }]
            }]
          },
          {
            "resource": {
              "attributes": [{"key":"service.name","value":{"stringValue":"causa-validate-server"}}]
            },
            "scopeSpans": [{
              "spans": [{
                "traceId": "aabbccddeeff00112233445566778899",
                "spanId": "0011223344556677",
                "parentSpanId": "aabbccddeeff0011",
                "name": "validate-server-span",
                "kind": 2,
                "startTimeUnixNano": "'"$(date +%s%N)"'",
                "endTimeUnixNano": "'"$(( $(date +%s%N) + 2000000 ))"'",
                "status": {"code": 1}
              }]
            }]
          }
        ]
      }' >/dev/null 2>&1 || true
  fi
  sleep 1
done

if [[ "${found}" -eq 1 ]]; then
  ok "Trace aabbccddeeff00112233445566778899 found in Tempo"
else
  fail "Trace not found in Tempo after ${max_wait}s — check OTel Collector → Tempo pipeline"
fi
echo ""
fi

# ── 9. Loki synthetic log push + query ────────────────────────────
# Push a synthetic log line and query it back within 30s.
# Tests: OTel Collector → Loki HTTP push, Loki chunk writer, Loki query path.
if [[ "${PROFILE}" == "all" || "${PROFILE}" == "app" ]]; then
echo "[9/13] Sending synthetic log to Loki and querying it back..."
pid_loki_check=$(port_forward_bg loki loki 3101 3100)
cleanup_pids+=("${pid_loki_check}")
sleep 2

LOG_TS="$(date +%s%N)"
LOKI_PUSH_RESP=$(curl --connect-timeout 2 --max-time 10 -sv \
  -X POST http://localhost:3101/loki/api/v1/push \
  -H 'Content-Type: application/json' \
  -d '{"streams":[{"stream":{"job":"causa-validate","env":"validate"},"values":[["'"${LOG_TS}"'","causa validate log line"]]}]}' \
  2>&1)

if echo "${LOKI_PUSH_RESP}" | grep -q '204\|HTTP/2 204\|HTTP/1.1 204'; then
  ok "Synthetic log line accepted by Loki (HTTP 204)"
else
  fail "Synthetic log push to Loki failed"
  echo "  Response: ${LOKI_PUSH_RESP}"
fi

# Query it back — Loki needs a small window to store the chunk.
loki_log_found=0
for i in $(seq 1 30); do
  LOKI_QUERY_RESP=$(curl --connect-timeout 2 --max-time 5 -sf \
    'http://localhost:3101/loki/api/v1/query_range?query=\{job%3D%22causa-validate%22\}&limit=1' \
    2>/dev/null || true)
  if [[ -n "${LOKI_QUERY_RESP}" ]] \
      && echo "${LOKI_QUERY_RESP}" | grep -q 'causa validate log line'; then
    loki_log_found=1
    break
  fi
  sleep 1
done

if [[ "${loki_log_found}" -eq 1 ]]; then
  ok "Synthetic log line queryable in Loki"
  info "Log ingestion pipeline: READY"
else
  fail "Synthetic log not found in Loki after 30s — check OTel Collector → Loki pipeline"
fi
echo ""
fi

# ── 10. Verify Tempo service graph metrics in Prometheus ────────────────────
if [[ "${PROFILE}" == "all" || "${PROFILE}" == "rca" ]]; then
echo "[10/13] Checking Stage 2 RCA readiness — Tempo service graph metrics in Prometheus..."
svc_graph_found=0
for i in $(seq 1 60); do
  SVC_GRAPH_RESP=$(curl --connect-timeout 2 --max-time 5 -sf \
    "http://localhost:9090/api/v1/query?query=traces_service_graph_request_total" \
    2>/dev/null || true)

  if [[ -n "${SVC_GRAPH_RESP}" ]] \
    && echo "${SVC_GRAPH_RESP}" | grep -q '"status":"success"' \
    && ! echo "${SVC_GRAPH_RESP}" | grep -q '"result":\[\]'; then
    svc_graph_found=1
    break
  fi
  sleep 1
done

if [[ "${svc_graph_found}" -eq 1 ]]; then
  ok "traces_service_graph_request_total found in Prometheus — Tempo metrics_generator is working"
  info "Stage 2 RCA Graph 1 edges: READY"
else
  fail "traces_service_graph_request_total not found in Prometheus after 60s"
  info "Tempo metrics_generator remote_write may not be configured or no compatible service graph spans have flowed yet"
fi
echo ""
fi

# ── 11. Alertmanager health + rules loaded ────────────────────────
if [[ "${PROFILE}" == "all" || "${PROFILE}" == "infra" ]]; then
echo "[11/13] Checking Alertmanager and Prometheus alert rules..."
pid=$(port_forward_bg alertmanager alertmanager 9093 9093)
cleanup_pids+=("${pid}")
sleep 2
if curl --connect-timeout 2 --max-time 5 -sf http://localhost:9093/-/ready &>/dev/null; then
  ok "Alertmanager /-/ready → 200 OK"
else
  fail "Alertmanager /-/ready did not respond"
fi

# Check that Prometheus loaded the alert rules
RULES_RESP=$(curl --connect-timeout 2 --max-time 5 -sf "http://localhost:9090/api/v1/rules" 2>/dev/null || true)
if [[ -n "${RULES_RESP}" ]] && echo "${RULES_RESP}" | grep -q '"ServiceHighErrorRate"'; then
  ok "Alert rules loaded in Prometheus (ServiceHighErrorRate confirmed)"
  info "Stage 2 alerting pipeline: READY"
else
  fail "Alert rules not found in Prometheus — check prometheus-rules ConfigMap"
fi
echo ""

# ── 10. Postgres health + schema ─────────────────────────────────
echo "[12/13] Checking Postgres RCA report storage..."
pid=$(port_forward_bg postgres postgres 5432 5432)
cleanup_pids+=("${pid}")
sleep 3

# pg_isready checks if Postgres is accepting connections
if k -n postgres exec deployment/postgres -- \
    pg_isready -U causa -d causa &>/dev/null; then
  ok "Postgres is accepting connections"
else
  fail "Postgres is not ready"
fi

# Verify rca_reports table was created by the init script
TBL_CHECK=$(k -n postgres exec deployment/postgres -- \
  psql -U causa -d causa -tAc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='rca_reports';" \
  2>/dev/null || echo "0")

if [[ "${TBL_CHECK}" == "1" ]]; then
  ok "rca_reports table exists — Stage 2 can write RCA reports"
  info "Stage 2 Postgres storage: READY"
else
  fail "rca_reports table not found in Postgres — check init ConfigMap"
fi
echo ""

# ── 11. Stage 2 RCA engine health ────────────────────────────────
echo "[13/13] Checking Stage 2 RCA engine webhook receiver..."
pid=$(port_forward_bg stage2-rca stage2-rca 8080 8080)
cleanup_pids+=("${pid}")
sleep 3
if curl --connect-timeout 2 --max-time 5 -sf http://localhost:8080/health &>/dev/null; then
  ok "Stage 2 RCA engine /health → 200 OK"
  info "Webhook trigger endpoint: POST http://localhost:8080/trigger"
else
  fail "Stage 2 RCA engine /health did not respond (not deployed yet?)"
fi
echo ""

echo "[14+] AM webhook → ingest, images, baselines, ingest health, node PromQL..."
AM_CFG=$(k -n alertmanager get configmap -o name 2>/dev/null | head -1 || true)
AM_WEBHOOK=""
if [[ -n "${AM_CFG}" ]]; then
  AM_WEBHOOK=$(k -n alertmanager get "${AM_CFG}" -o yaml 2>/dev/null | grep -o 'http[^ ]*causa-ingest[^ ]*' | head -1 || true)
fi
if [[ -z "${AM_WEBHOOK}" ]]; then
  AM_WEBHOOK=$(k -n alertmanager get secret -o name 2>/dev/null | head -1 || true)
  # Fall back: check alertmanager values via pod args / mounted file
  AM_POD=$(k -n alertmanager get pod -l app.kubernetes.io/name=alertmanager -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || \
    k -n alertmanager get pod -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [[ -n "${AM_POD}" ]]; then
    AM_WEBHOOK=$(k -n alertmanager exec "${AM_POD}" -- \
      sh -c 'grep -rho "causa-ingest[^ ]*" /etc/alertmanager 2>/dev/null | head -1' 2>/dev/null || true)
  fi
fi
if echo "${AM_WEBHOOK}" | grep -q 'causa-ingest'; then
  ok "Alertmanager webhook targets causa-ingest"
else
  fail "Alertmanager webhook does not target causa-ingest (got: ${AM_WEBHOOK:-empty})"
fi

S2_IMG=$(k -n stage2-rca get deploy stage2-rca -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
ING_IMG=$(k -n causa-ingest get deploy causa-ingest -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
if echo "${S2_IMG}" | grep -q 'causa/stage2-rca' && ! echo "${S2_IMG}" | grep -qi 'traceops'; then
  ok "stage2 image is causa/stage2-rca (${S2_IMG})"
else
  fail "stage2 image unexpected: ${S2_IMG:-missing}"
fi
if echo "${ING_IMG}" | grep -q 'causa/ingest' && ! echo "${ING_IMG}" | grep -qi 'traceops'; then
  ok "ingest image is causa/ingest (${ING_IMG})"
else
  fail "ingest image unexpected: ${ING_IMG:-missing}"
fi

BL=$(k -n postgres exec deployment/postgres -- \
  psql -U causa -d causa -tAc "SELECT COUNT(*) FROM anomaly_baseline;" 2>/dev/null | tr -d '[:space:]' || echo "0")
if [[ "${BL}" =~ ^[0-9]+$ ]] && [[ "${BL}" -gt 0 ]]; then
  ok "anomaly_baseline has ${BL} row(s)"
else
  fail "anomaly_baseline empty — capture_baseline --write-db required"
fi

pid=$(port_forward_bg causa-ingest causa-ingest 8082 8080)
cleanup_pids+=("${pid}")
sleep 2
if curl --connect-timeout 2 --max-time 5 -sf http://localhost:8082/health &>/dev/null; then
  ok "causa-ingest /health → 200 OK"
else
  fail "causa-ingest /health did not respond"
fi

NODE_Q=$(curl --connect-timeout 2 --max-time 5 -sf \
  'http://localhost:9090/api/v1/query?query=node_cpu_seconds_total' 2>/dev/null || true)
if [[ -n "${NODE_Q}" ]] && echo "${NODE_Q}" | grep -q '"status":"success"' \
  && ! echo "${NODE_Q}" | grep -q '"result":\[\]'; then
  ok "node PromQL non-empty (node_cpu_seconds_total)"
else
  fail "node PromQL empty — node-exporter / scrape broken"
fi

UQ=$(k -n postgres exec deployment/postgres -- \
  psql -U causa -d causa -tAc \
  "SELECT COUNT(*) FROM pg_indexes WHERE indexname='uq_rca_incident_id';" 2>/dev/null | tr -d '[:space:]' || echo "0")
if [[ "${UQ}" == "1" ]]; then
  ok "uq_rca_incident_id index present"
else
  fail "uq_rca_incident_id missing — schema-migrate required"
fi
echo ""
fi

# ── Result summary ─────────────────────────────────────────
echo "========================================================"
echo "  Validation Summary"
echo "========================================================"
echo "  Passed: ${PASS}"
echo "  Failed: ${FAIL}"
echo ""
if [[ "${FAIL}" -eq 0 ]]; then
  echo "  ALL CHECKS PASSED — Dev A+B+C stack ready"
  exit 0
else
  echo "  SOME CHECKS FAILED — Review output above"
  exit 1
fi
