#!/usr/bin/env bash
# causa-startup.sh — Start Kind (if needed), ensure Causa observability stack,
# port-forwards, Watchdog API (:8090), and UI (:5174).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ID_ROOT="${ROOT}/incident-detection"
DASH="${ROOT}/dashboard"
FE="${DASH}/frontend"
CONTEXT="${CONTEXT:-kind-gcp-demo-test}"
KIND_CLUSTER="${KIND_CLUSTER:-gcp-demo-test}"
APP_NS="${APP_NS:-${CAUSA_APP_NAMESPACE:-otel-demo}}"
API_PORT="${API_PORT:-8090}"
UI_PORT="${UI_PORT:-5174}"
PID_DIR="${ROOT}/.run"
LOG_DIR="${PID_DIR}/logs"

# Load local secrets (NVIDIA NIM, etc.) without printing values.
if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi

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

ensure_stack() {
  if kubectl --context "${CONTEXT}" -n prometheus get svc prometheus &>/dev/null \
    && kubectl --context "${CONTEXT}" -n otel-agent get ds otel-agent &>/dev/null; then
    log "Causa observability stack already present"
    return 0
  fi
  log "Deploying Causa observability stack (dev)"
  need helm
  "${ID_ROOT}/scripts/deploy.sh" dev
}

free_port() {
  local port="$1"
  local pids
  pids="$(lsof -ti tcp:"${port}" 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    log "Freeing port ${port}"
    # shellcheck disable=SC2086
    kill ${pids} 2>/dev/null || true
    sleep 0.4
  fi
}

start_pf() {
  local ns="$1" svc="$2" local_port="$3" remote_port="$4"
  free_port "${local_port}"
  # setsid: survive parent shell exit (Cursor/agent shells send SIGHUP otherwise)
  setsid kubectl --context "${CONTEXT}" -n "${ns}" port-forward "svc/${svc}" "${local_port}:${remote_port}" \
    </dev/null >"${LOG_DIR}/pf-${ns}.log" 2>&1 &
  echo $! >"${PID_DIR}/pf-${ns}.pid"
  log "Port-forward ${ns}/${svc} → localhost:${local_port}"
}

ensure_venv() {
  if [[ ! -x "${DASH}/.venv/bin/uvicorn" ]]; then
    log "Creating dashboard venv + installing deps"
    python3 -m venv "${DASH}/.venv"
    "${DASH}/.venv/bin/pip" install -r "${DASH}/backend/requirements.txt"
  fi
}

ensure_frontend_deps() {
  if [[ ! -d "${FE}/node_modules" ]]; then
    log "Installing frontend npm deps"
    (cd "${FE}" && npm install)
  fi
}

start_api() {
  free_port "${API_PORT}"
  ensure_venv
  setsid bash -c "
    cd '${ROOT}'
    export PYTHONPATH='${ROOT}:${ID_ROOT}:${PYTHONPATH:-}'
    export CAUSA_DASHBOARD_OUTPUTS='${CAUSA_DASHBOARD_OUTPUTS:-${ROOT}/outputs}'
    export PROMETHEUS_URL='http://127.0.0.1:9090'
    export LOKI_URL='http://127.0.0.1:3100'
    export TEMPO_URL='http://127.0.0.1:3200'
    export PGHOST=127.0.0.1 PGPORT=15432 PGDATABASE=causa PGUSER='${PGUSER:-rca_engine}'
    export REMEDIATION_PGHOST=127.0.0.1 REMEDIATION_PGPORT=15432 REMEDIATION_PGDATABASE=causa REMEDIATION_PGUSER='${REMEDIATION_PGUSER:-causa_remediator}'
    export CONTEXT='${CONTEXT}'
    export CAUSA_NIM_ENABLED='${CAUSA_NIM_ENABLED:-1}'
    export CAUSA_REMEDIATION_KILL_SWITCH='${CAUSA_REMEDIATION_KILL_SWITCH:-0}'
    if [[ -f '${ROOT}/.env' ]]; then set -a; source '${ROOT}/.env'; set +a; fi
    source '${ROOT}/scripts/load-dashboard-demo-env.sh'
    load_dashboard_demo_env
    exec '${DASH}/.venv/bin/uvicorn' dashboard.backend.main:app --host 0.0.0.0 --port '${API_PORT}'
  " </dev/null >"${LOG_DIR}/api.log" 2>&1 &
  echo $! >"${PID_DIR}/api.pid"
  log "API → http://127.0.0.1:${API_PORT}"
}

start_remediation_worker() {
  setsid bash -c "
    cd '${ROOT}'
    export PYTHONPATH='${ROOT}:${ID_ROOT}:${PYTHONPATH:-}'
    export CAUSA_DASHBOARD_OUTPUTS='${CAUSA_DASHBOARD_OUTPUTS:-${ROOT}/outputs}'
    export PROMETHEUS_URL='http://127.0.0.1:9090'
    export PGHOST=127.0.0.1 PGPORT=15432 PGDATABASE=causa PGUSER='${PGUSER:-rca_engine}'
    export REMEDIATION_PGHOST=127.0.0.1 REMEDIATION_PGPORT=15432 REMEDIATION_PGDATABASE=causa REMEDIATION_PGUSER='${REMEDIATION_PGUSER:-causa_remediator}'
    export CONTEXT='${CONTEXT}'
    export REMEDIATION_NAMESPACE='${REMEDIATION_NAMESPACE:-${APP_NS}}'
    export REMEDIATION_WORKER_POLL_S='${REMEDIATION_WORKER_POLL_S:-5}'
    export CAUSA_REMEDIATION_KILL_SWITCH='${CAUSA_REMEDIATION_KILL_SWITCH:-0}'
    if [[ -f '${ROOT}/.env' ]]; then set -a; source '${ROOT}/.env'; set +a; fi
    source '${ROOT}/scripts/load-dashboard-demo-env.sh'
    load_dashboard_demo_env
    exec '${DASH}/.venv/bin/python' -m remediation.worker
  " </dev/null >"${LOG_DIR}/remediation-worker.log" 2>&1 &
  echo $! >"${PID_DIR}/remediation-worker.pid"
  log "Remediation worker started (pid file ${PID_DIR}/remediation-worker.pid)"
}

start_ui() {
  free_port "${UI_PORT}"
  ensure_frontend_deps
  setsid bash -c "
    cd '${FE}'
    exec npm run dev
  " </dev/null >"${LOG_DIR}/ui.log" 2>&1 &
  echo $! >"${PID_DIR}/ui.pid"
  log "UI  → http://127.0.0.1:${UI_PORT}"
}

wait_http() {
  local url="$1" name="$2" n=0
  until curl -sf "${url}" >/dev/null 2>&1; do
    n=$((n + 1))
    [[ "${n}" -lt 40 ]] || die "${name} did not become ready (${url})"
    sleep 0.5
  done
}

main() {
  echo "========================================================"
  echo "  Causa startup (stack + Watchdog dashboard)"
  echo "  context=${CONTEXT}"
  echo "========================================================"

  mkdir -p "${PID_DIR}" "${LOG_DIR}"
  ensure_kind
  ensure_stack

  start_pf prometheus prometheus 9090 9090
  start_pf loki loki 3100 3100
  start_pf tempo tempo 3200 3200
  start_pf postgres postgres 15432 5432
  if kubectl --context "${CONTEXT}" -n stage2-rca get svc stage2-rca &>/dev/null; then
    start_pf stage2-rca stage2-rca 18080 8080
  fi
  sleep 2

  start_api
  start_ui
  if [[ "${START_LOCAL_REMEDIATION_WORKER:-0}" == "1" ]]; then
    start_remediation_worker
  else
    log "Local remediation worker disabled (set START_LOCAL_REMEDIATION_WORKER=1 to opt in)"
  fi
  wait_http "http://127.0.0.1:${API_PORT}/health" "API"

  local baseline_n
  baseline_n="$(
    source "${ROOT}/scripts/load-dashboard-demo-env.sh"
    load_dashboard_demo_env
    PGPASSWORD="${PGPASSWORD:-}" psql -h 127.0.0.1 -p 15432 -U "${PGUSER:-rca_engine}" -d "${PGDATABASE:-causa}" -Atc 'SELECT COUNT(*) FROM anomaly_baseline' 2>/dev/null || echo 0
  )"
  if [[ "${baseline_n}" == "0" || -z "${baseline_n}" ]]; then
    log "WARNING: anomaly_baseline is empty — RCA will abstain until baselines exist"
    log "Run after demo is healthy: SETTLE_SEC=30 WINDOW_MIN=20 ./scripts/recapture-baselines.sh"
  else
    log "anomaly_baseline rows: ${baseline_n}"
  fi

  echo ""
  echo "Causa is up:"
  echo "  UI     http://127.0.0.1:${UI_PORT}"
  echo "  API    http://127.0.0.1:${API_PORT}"
  echo "  Prom   http://127.0.0.1:9090"
  echo "  Loki   http://127.0.0.1:3100"
  echo "  Tempo  http://127.0.0.1:3200"
  echo "  PG     127.0.0.1:15432"
  [[ "${START_LOCAL_REMEDIATION_WORKER:-0}" == "1" ]] && echo "  Worker remediation (claims approved plans)"
  echo ""
  echo "PIDs under ${PID_DIR}/  logs under ${LOG_DIR}/"
  echo "Demo apps: ./microservice-startup.sh"
  echo "Argo canary: ./incident-detection/scripts/install-argo-rollouts.sh"
  echo "Stop local processes: pkill -f 'dashboard.backend.main|remediation.worker|causa/dashboard/frontend.*vite|kubectl -n (prometheus|loki|tempo|postgres|stage2-rca) port-forward'"
}

main "$@"
