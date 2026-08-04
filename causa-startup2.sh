#!/usr/bin/env bash
# One-command Machine A launcher for Causa + remote OTLP ingestion.
#
# Defaults:
#   Kind cluster:  causa-local
#   Kube context:  kind-causa-local
#   OTLP ingress:  127.0.0.1:4317 (gRPC), 127.0.0.1:4318 (HTTP)
#   Dashboard:     http://127.0.0.1:5174
#
# Machine B connects after this script is ready:
#   ssh -N -L 127.0.0.1:14317:127.0.0.1:4317 <user>@<machine-a>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPTS="${ROOT}/deploy/scripts"
DASHBOARD_ROOT="${ROOT}/dashboard"
FRONTEND_ROOT="${DASHBOARD_ROOT}/frontend"
IMPORT_ROOT="${ROOT}/.import"

KIND_CLUSTER="${KIND_CLUSTER:-causa-local}"
KUBECTL_CONTEXT="${CONTEXT:-kind-${KIND_CLUSTER}}"
DEPLOY_ENV="${DEPLOY_ENV:-dev}"
STAGE2_PROFILE="${STAGE2_PROFILE:-real}"
REBUILD_IMAGES="${REBUILD_IMAGES:-auto}"
REDEPLOY_STACK="${REDEPLOY_STACK:-0}"
START_LOCAL_REMEDIATION_WORKER="${START_LOCAL_REMEDIATION_WORKER:-0}"
IMAGES_REBUILT=0

API_PORT="${API_PORT:-8090}"
UI_PORT="${UI_PORT:-5174}"
PID_DIR="${ROOT}/.run"
LOG_DIR="${PID_DIR}/logs"

# Capture safe remote-remediation settings before loading a repository .env.
# The OTLP tunnel is a data plane, not authorization to mutate Machine B.
REMOTE_AUTO_PROPOSE="${CAUSA_REMEDIATION_AUTO_PROPOSE:-1}"
REMOTE_AUTO_APPLY="${CAUSA_REMEDIATION_AUTO_APPLY:-0}"
REMOTE_ALLOW_INFRA="${CAUSA_REMEDIATION_ALLOW_INFRA:-0}"
REMOTE_KILL_SWITCH="${CAUSA_REMEDIATION_KILL_SWITCH:-0}"

log() { printf '==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }

k() {
  kubectl --context "${KUBECTL_CONTEXT}" "$@"
}

check_prerequisites() {
  local tool
  for tool in docker kind kubectl helm curl setsid ps awk python3 npm jq base64; do
    need "${tool}"
  done
  if ! command -v lsof >/dev/null 2>&1 && ! command -v ss >/dev/null 2>&1; then
    die "either lsof or ss is required for safe local-port checks"
  fi
  docker info >/dev/null 2>&1 || die "Docker is installed but its daemon is not reachable"
  [[ "${DEPLOY_ENV}" == "dev" || "${DEPLOY_ENV}" == "prod" ]] || die "DEPLOY_ENV must be dev or prod"
  [[ "${STAGE2_PROFILE}" == "real" || "${STAGE2_PROFILE}" == "mock" ]] || die "STAGE2_PROFILE must be real or mock"
  if [[ "${DEPLOY_ENV}" == "prod" && "${STAGE2_PROFILE}" == "mock" ]]; then
    die "the Stage-2 mock is development-only"
  fi
}

ensure_dashboard_secrets() {
  if [[ "${DEPLOY_ENV}" == "dev" ]]; then
    log "Ensuring local dashboard/remediation database credentials"
    "${ROOT}/scripts/bootstrap-local-demo-secrets.sh"
  else
    log "Production mode: expecting dashboard database credentials from operator-managed secrets or .env"
  fi
}

ensure_application_namespace() {
  # The Stage-2 chart always creates its least-privilege planner Role and
  # RoleBinding in `remediation.namespace` (otel-demo by default). In the
  # remote-machine topology the application is not in this Kind cluster, but
  # Kubernetes still requires the target namespace to exist before Helm can
  # create those RBAC objects.
  log "Ensuring Stage 2 planner namespace otel-demo"
  k create namespace otel-demo --dry-run=client -o yaml | k apply -f - >/dev/null
}

ensure_kind_cluster() {
  if ! kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER}"; then
    log "Creating Kind cluster ${KIND_CLUSTER}"
    kind create cluster --name "${KIND_CLUSTER}"
  else
    local node state
    while IFS= read -r node; do
      [[ -n "${node}" ]] || continue
      state="$(docker inspect -f '{{.State.Status}}' "${node}" 2>/dev/null || true)"
      if [[ "${state}" != "running" ]]; then
        log "Starting Kind node ${node}"
        docker start "${node}" >/dev/null
      fi
    done < <(kind get nodes --name "${KIND_CLUSTER}")
  fi

  kubectl config use-context "${KUBECTL_CONTEXT}" >/dev/null
  local attempts=0
  until k cluster-info --request-timeout=5s >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    [[ "${attempts}" -lt 60 ]] || die "Kind cluster ${KIND_CLUSTER} did not become reachable"
    sleep 2
  done
  log "Kubernetes context ${KUBECTL_CONTEXT} is ready"
}

docker_image_exists() {
  docker image inspect "$1" >/dev/null 2>&1
}

ensure_causa_images() {
  local must_build=0
  case "${REBUILD_IMAGES}" in
    1|true|yes) must_build=1 ;;
    0|false|no) must_build=0 ;;
    auto)
      docker_image_exists causa/ingest:local || must_build=1
      docker_image_exists causa/stage2-rca:local || must_build=1
      ;;
    *) die "REBUILD_IMAGES must be auto, 1, or 0" ;;
  esac

  if [[ "${must_build}" == "1" ]]; then
    log "Building Causa application images"
    "${DEPLOY_SCRIPTS}/build_images.sh" --kind-cluster "${KIND_CLUSTER}" --no-load
    docker tag causa/ingest:latest causa/ingest:local
    docker tag causa/stage2-rca:latest causa/stage2-rca:local
    IMAGES_REBUILT=1
  fi

  local image
  for image in causa/ingest:local causa/stage2-rca:local; do
    docker_image_exists "${image}" || die "missing ${image}; rerun with REBUILD_IMAGES=1"
    log "Loading ${image} into Kind cluster ${KIND_CLUSTER}"
    kind load docker-image "${image}" --name "${KIND_CLUSTER}"
  done
}

refresh_causa_workloads() {
  [[ "${IMAGES_REBUILT}" == "1" ]] || return 0
  log "Restarting Causa workloads with freshly built images"
  k -n causa-ingest rollout restart deployment/causa-ingest >/dev/null
  k -n stage2-rca rollout restart deployment/stage2-rca >/dev/null
}

enforce_stage2_remote_safety() {
  # values-dev enables local-demo auto-apply. Remote telemetry does not grant
  # control of Machine B, so retain automatic proposals but never auto-approve.
  log "Applying remote-safe Stage 2 remediation settings"
  k -n stage2-rca set env deployment/stage2-rca \
    "CAUSA_REMEDIATION_AUTO_PROPOSE=${REMOTE_AUTO_PROPOSE}" \
    "CAUSA_REMEDIATION_AUTO_APPLY=${REMOTE_AUTO_APPLY}" \
    "CAUSA_REMEDIATION_ALLOW_INFRA=${REMOTE_ALLOW_INFRA}" \
    "CAUSA_REMEDIATION_KILL_SWITCH=${REMOTE_KILL_SWITCH}" \
    CAUSA_DEMO_ALLOW_UNCALIBRATED_AUTO_APPLY=0 \
    CAUSA_REMEDIATION_AUTO_ACTIONS- >/dev/null
  if [[ "${REMOTE_AUTO_APPLY}" != "0" ]]; then
    warn "CAUSA_REMEDIATION_AUTO_APPLY is enabled explicitly; this is unsafe for the remote demo"
  fi
}

stack_is_present() {
  k -n otel-collector get svc otel-collector >/dev/null 2>&1 \
    && k -n stage2-rca get svc stage2-rca >/dev/null 2>&1 \
    && k -n causa-ingest get svc causa-ingest >/dev/null 2>&1 \
    && k -n postgres get svc postgres >/dev/null 2>&1 \
    && [[ "$(helm --kube-context "${KUBECTL_CONTEXT}" status stage2-rca -n stage2-rca -o json 2>/dev/null | jq -r '.info.status // empty')" == "deployed" ]] \
    && [[ "$(helm --kube-context "${KUBECTL_CONTEXT}" status causa-ingest -n causa-ingest -o json 2>/dev/null | jq -r '.info.status // empty')" == "deployed" ]]
}

ensure_stack() {
  if stack_is_present && [[ "${REDEPLOY_STACK}" != "1" ]]; then
    log "Causa Kubernetes stack is already deployed"
    return
  fi
  log "Deploying the complete Causa ${DEPLOY_ENV} stack"
  (
    cd "${ROOT}"
    CONTEXT="${KUBECTL_CONTEXT}" ./deploy/scripts/deploy.sh \
      "${DEPLOY_ENV}" --stage2 "${STAGE2_PROFILE}"
  )
}

wait_for_workloads() {
  local namespace workload
  local -a workloads=(
    kube-state-metrics/kube-state-metrics
    node-exporter/daemonset/node-exporter
    minio-tempo/minio-tempo
    tempo/tempo
    minio-loki/minio-loki
    loki/loki
    minio-mimir/minio-mimir
    mimir/mimir
    prometheus/prometheus
    otel-collector/otel-collector
    otel-lb/otel-lb
    otel-agent/daemonset/otel-agent
    alertmanager/alertmanager
    postgres/postgres
    stage2-rca/stage2-rca
    causa-ingest/causa-ingest
  )

  log "Waiting for Causa workloads"
  for workload in "${workloads[@]}"; do
    namespace="${workload%%/*}"
    workload="${workload#*/}"
    if [[ "${workload}" == daemonset/* ]]; then
      k -n "${namespace}" rollout status "${workload}" --timeout=5m
    else
      k -n "${namespace}" rollout status "deployment/${workload}" --timeout=5m
    fi
  done
}

pid_is_managed() {
  local file="$1" expected="$2" pid command_line
  [[ -f "${file}" ]] || return 1
  pid="$(<"${file}")"
  [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null || return 1
  command_line="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  [[ "${command_line}" == *"${expected}"* ]]
}

port_is_busy() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t >/dev/null 2>&1
  else
    ss -ltn "( sport = :${port} )" 2>/dev/null | tail -n +2 | grep -q .
  fi
}

assert_ports_available() {
  local port
  for port in "$@"; do
    port_is_busy "${port}" && die "localhost:${port} is already in use by an unmanaged process"
  done
  return 0
}

start_forward() {
  local name="$1" namespace="$2" service="$3"
  shift 3
  local pid_file="${PID_DIR}/pf-${name}.pid"
  local log_file="${LOG_DIR}/pf-${name}.log"
  local expected="--context ${KUBECTL_CONTEXT}"
  local mapping local_port

  if pid_is_managed "${pid_file}" "${expected}"; then
    log "Reusing ${name} port-forward"
    return
  fi
  rm -f "${pid_file}"

  for mapping in "$@"; do
    local_port="$(awk -F: '{print $(NF-1)}' <<< "${mapping}")"
    assert_ports_available "${local_port}"
  done

  setsid kubectl --context "${KUBECTL_CONTEXT}" -n "${namespace}" \
    port-forward --address=127.0.0.1 "svc/${service}" "$@" \
    </dev/null >"${log_file}" 2>&1 &
  echo $! >"${pid_file}"
  log "Port-forward ${namespace}/${service}: $*"
}

wait_http() {
  local url="$1" name="$2" attempts=0
  until curl --fail --silent "${url}" >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    [[ "${attempts}" -lt 60 ]] || die "${name} did not become ready at ${url}; see ${LOG_DIR}"
    sleep 1
  done
}

wait_tcp() {
  local host="$1" port="$2" name="$3" attempts=0
  until (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; do
    attempts=$((attempts + 1))
    [[ "${attempts}" -lt 60 ]] || die "${name} did not open ${host}:${port}; see ${LOG_DIR}"
    sleep 1
  done
}

start_port_forwards() {
  start_forward otel-collector otel-collector otel-collector \
    4317:4317 4318:4318 13133:13133
  start_forward prometheus prometheus prometheus 9090:9090
  start_forward loki loki loki 3100:3100
  start_forward tempo tempo tempo 3200:3200
  start_forward postgres postgres postgres 15432:5432
  start_forward stage2-rca stage2-rca stage2-rca 18080:8080

  wait_http http://127.0.0.1:13133/ "OTel Collector"
  wait_http http://127.0.0.1:9090/-/ready "Prometheus"
  wait_http http://127.0.0.1:3100/ready "Loki"
  wait_http http://127.0.0.1:3200/ready "Tempo"
  wait_http http://127.0.0.1:18080/health "Stage 2"
  wait_tcp 127.0.0.1 15432 Postgres
}

ensure_dashboard_dependencies() {
  if [[ ! -x "${DASHBOARD_ROOT}/.venv/bin/uvicorn" ]]; then
    log "Creating dashboard Python environment"
    python3 -m venv "${DASHBOARD_ROOT}/.venv"
    "${DASHBOARD_ROOT}/.venv/bin/pip" install -r "${DASHBOARD_ROOT}/backend/requirements.txt"
  fi
  if [[ ! -d "${FRONTEND_ROOT}/node_modules" ]]; then
    log "Installing dashboard frontend dependencies"
    (cd "${FRONTEND_ROOT}" && npm ci)
  fi
}

start_dashboard_api() {
  local pid_file="${PID_DIR}/api.pid"
  if pid_is_managed "${pid_file}" "dashboard.backend.main"; then
    log "Reusing dashboard API"
    return
  fi
  rm -f "${pid_file}"
  assert_ports_available "${API_PORT}"

  setsid bash -c "
    cd '${ROOT}'
    if [[ -f '${ROOT}/.env' ]]; then set -a; source '${ROOT}/.env'; set +a; fi
    export PYTHONPATH='${IMPORT_ROOT}:${ROOT}:${PYTHONPATH:-}'
    export CAUSA_DASHBOARD_OUTPUTS='${CAUSA_DASHBOARD_OUTPUTS:-${ROOT}/outputs}'
    export PROMETHEUS_URL='http://127.0.0.1:9090'
    export LOKI_URL='http://127.0.0.1:3100'
    export TEMPO_URL='http://127.0.0.1:3200'
    export PGHOST=127.0.0.1 PGPORT=15432 PGDATABASE=causa PGUSER='${PGUSER:-rca_engine}'
    export REMEDIATION_PGHOST=127.0.0.1 REMEDIATION_PGPORT=15432 REMEDIATION_PGDATABASE=causa
    export REMEDIATION_PGUSER='${REMEDIATION_PGUSER:-causa_remediator}'
    export CONTEXT='${KUBECTL_CONTEXT}'
    export CAUSA_REMEDIATION_AUTO_PROPOSE='${CAUSA_REMEDIATION_AUTO_PROPOSE}'
    export CAUSA_REMEDIATION_AUTO_APPLY='${CAUSA_REMEDIATION_AUTO_APPLY}'
    export CAUSA_REMEDIATION_ALLOW_INFRA='${CAUSA_REMEDIATION_ALLOW_INFRA}'
    export CAUSA_REMEDIATION_KILL_SWITCH='${CAUSA_REMEDIATION_KILL_SWITCH}'
    source '${ROOT}/scripts/load-dashboard-demo-env.sh'
    load_dashboard_demo_env
    exec '${DASHBOARD_ROOT}/.venv/bin/uvicorn' dashboard.backend.main:app --host 127.0.0.1 --port '${API_PORT}'
  " </dev/null >"${LOG_DIR}/api.log" 2>&1 &
  echo $! >"${pid_file}"
  log "Dashboard API → http://127.0.0.1:${API_PORT}"
}

start_dashboard_ui() {
  local pid_file="${PID_DIR}/ui.pid"
  if pid_is_managed "${pid_file}" "npm run dev"; then
    log "Reusing dashboard UI"
    return
  fi
  rm -f "${pid_file}"
  assert_ports_available "${UI_PORT}"

  setsid bash -c "cd '${FRONTEND_ROOT}' && exec npm run dev -- --host 127.0.0.1 --port '${UI_PORT}'" \
    </dev/null >"${LOG_DIR}/ui.log" 2>&1 &
  echo $! >"${pid_file}"
  log "Dashboard UI → http://127.0.0.1:${UI_PORT}"
}

start_remediation_worker() {
  [[ "${START_LOCAL_REMEDIATION_WORKER}" == "1" ]] || {
    log "Remediation worker disabled; plans remain viewable/propose-only"
    return
  }

  local pid_file="${PID_DIR}/remediation-worker.pid"
  if pid_is_managed "${pid_file}" "remediation.worker"; then
    log "Reusing remediation worker"
    return
  fi

  rm -f "${pid_file}"
  setsid bash -c "
    cd '${ROOT}'
    if [[ -f '${ROOT}/.env' ]]; then set -a; source '${ROOT}/.env'; set +a; fi
    export PYTHONPATH='${IMPORT_ROOT}:${ROOT}:${PYTHONPATH:-}'
    export PGHOST=127.0.0.1 PGPORT=15432 PGDATABASE=causa PGUSER='${PGUSER:-rca_engine}'
    export REMEDIATION_PGHOST=127.0.0.1 REMEDIATION_PGPORT=15432 REMEDIATION_PGDATABASE=causa
    export REMEDIATION_PGUSER='${REMEDIATION_PGUSER:-causa_remediator}'
    export CONTEXT='${KUBECTL_CONTEXT}'
    export CAUSA_REMEDIATION_AUTO_PROPOSE='${CAUSA_REMEDIATION_AUTO_PROPOSE}'
    export CAUSA_REMEDIATION_AUTO_APPLY='${CAUSA_REMEDIATION_AUTO_APPLY}'
    export CAUSA_REMEDIATION_ALLOW_INFRA='${CAUSA_REMEDIATION_ALLOW_INFRA}'
    export CAUSA_REMEDIATION_KILL_SWITCH='${CAUSA_REMEDIATION_KILL_SWITCH}'
    source '${ROOT}/scripts/load-dashboard-demo-env.sh'
    load_dashboard_demo_env
    exec '${DASHBOARD_ROOT}/.venv/bin/python' -m remediation.worker
  " </dev/null >"${LOG_DIR}/remediation-worker.log" 2>&1 &
  echo $! >"${pid_file}"
  log "Local remediation worker started in kill-switch mode"
}

print_summary() {
  local ssh_user machine_host
  ssh_user="${SSH_USER:-${USER:-$(id -un)}}"
  machine_host="${MACHINE_A_HOST:-$(hostname -f 2>/dev/null || hostname)}"

  cat <<EOF

========================================================
  Causa Machine A is ready
========================================================
  Dashboard:    http://127.0.0.1:${UI_PORT}
  API:          http://127.0.0.1:${API_PORT}
  OTLP gRPC:    127.0.0.1:4317
  OTLP HTTP:    127.0.0.1:4318
  Prometheus:   http://127.0.0.1:9090
  Loki:         http://127.0.0.1:3100
  Tempo:        http://127.0.0.1:3200
  Stage 2:      http://127.0.0.1:18080

Run this on Machine B and keep it running:
  ssh -N -L 127.0.0.1:14317:127.0.0.1:4317 ${ssh_user}@${machine_host}

Machine B collector endpoint:
  CAUSA_OTLP_ENDPOINT=host.docker.internal:14317

Safety: remediation auto-apply and infrastructure actions are disabled.
Stage 2 auto-proposes plans for dashboard review; no worker applies them.
RCA requires a healthy baseline captured after Machine B begins sending data.
Logs and PID files: ${PID_DIR}
Stop local processes: ./causa-stop2.sh
Delete the Kind cluster only when explicitly desired:
  DELETE_CLUSTER=1 ./causa-stop2.sh
EOF
}

main() {
  check_prerequisites
  mkdir -p "${PID_DIR}" "${LOG_DIR}"

  if [[ -f "${ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${ROOT}/.env"
    set +a
  fi

  # Reassert the captured remote-safe values after loading .env.
  export CAUSA_REMEDIATION_AUTO_PROPOSE="${REMOTE_AUTO_PROPOSE}"
  export CAUSA_REMEDIATION_AUTO_APPLY="${REMOTE_AUTO_APPLY}"
  export CAUSA_REMEDIATION_ALLOW_INFRA="${REMOTE_ALLOW_INFRA}"
  export CAUSA_REMEDIATION_KILL_SWITCH="${REMOTE_KILL_SWITCH}"

  ensure_kind_cluster
  ensure_application_namespace
  ensure_dashboard_secrets
  ensure_causa_images
  ensure_stack
  refresh_causa_workloads
  enforce_stage2_remote_safety
  wait_for_workloads
  start_port_forwards
  ensure_dashboard_dependencies
  start_dashboard_api
  start_dashboard_ui
  start_remediation_worker
  wait_http "http://127.0.0.1:${API_PORT}/api/health" "Dashboard API and database"
  wait_http "http://127.0.0.1:${UI_PORT}/" "Dashboard UI"
  print_summary
}

main "$@"
