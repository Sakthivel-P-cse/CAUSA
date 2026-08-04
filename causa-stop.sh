#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIND_CLUSTER="${KIND_CLUSTER:-gcp-demo-test}"
KIND_NODE="${KIND_NODE:-${KIND_CLUSTER}-control-plane}"
PID_DIR="${ROOT}/.run"

log() { printf '==> %s\n' "$*"; }

stop_pid_file() {
  local file="$1" expected="$2" pid cmd
  [[ -f "${file}" ]] || return 0
  pid="$(cat "${file}" 2>/dev/null || true)"
  if [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null; then
    cmd="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
    if [[ "${cmd}" == *"${expected}"* ]]; then
      kill "${pid}" 2>/dev/null || true
      log "Stopped $(basename "${file}" .pid) (${pid})"
    else
      log "Ignored stale $(basename "${file}")"
    fi
  fi
  rm -f "${file}"
}

stop_causa_processes() {
  local file pid cwd cmd

  stop_pid_file "${PID_DIR}/api.pid" "dashboard.backend.main"
  stop_pid_file "${PID_DIR}/ui.pid" "vite"
  stop_pid_file "${PID_DIR}/remediation-worker.pid" "remediation.worker"

  for file in "${PID_DIR}"/pf-*.pid; do
    [[ -e "${file}" ]] || continue
    stop_pid_file "${file}" "port-forward"
  done

  while read -r pid; do
    [[ -n "${pid}" && "${pid}" != "$$" ]] || continue
    cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
    cmd="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
    if [[ "${cwd}" == "${ROOT}"* ]] && [[ "${cmd}" == *"dashboard.backend.main"* || "${cmd}" == *"remediation.worker"* || "${cmd}" == *"/dashboard/frontend/"*"vite"* ]]; then
      kill "${pid}" 2>/dev/null || true
    fi
  done < <(pgrep -f 'dashboard\.backend\.main|remediation\.worker|dashboard/frontend/.*/vite' 2>/dev/null || true)

  sleep 1
}

stop_kind() {
  if ! command -v docker >/dev/null 2>&1; then
    log "Docker not found; skipped Kind node"
    return
  fi
  if [[ "$(docker inspect -f '{{.State.Running}}' "${KIND_NODE}" 2>/dev/null || true)" == "true" ]]; then
    docker stop "${KIND_NODE}" >/dev/null
    log "Stopped Kind node ${KIND_NODE} (Causa and microservices)"
  else
    log "Kind node ${KIND_NODE} is already stopped"
  fi
}

stop_causa_processes
stop_kind
log "Causa and microservices are stopped"


