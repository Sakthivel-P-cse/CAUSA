#!/usr/bin/env bash
# Stop host-side processes created by causa-startup2.sh and stop the Kind
# Docker node(s) to free RAM while preserving the deployed cluster.
#
# Set KEEP_CLUSTER_RUNNING=1 for a fast restart without releasing cluster RAM.
# Set DELETE_CLUSTER=1 to permanently delete the dedicated Kind cluster.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="${ROOT}/.run"
KIND_CLUSTER="${KIND_CLUSTER:-causa-local}"
DELETE_CLUSTER="${DELETE_CLUSTER:-0}"
KEEP_CLUSTER_RUNNING="${KEEP_CLUSTER_RUNNING:-0}"

log() { printf '==> %s\n' "$*"; }

stop_pid_file() {
  local file="$1" expected="$2" pid command_line
  [[ -f "${file}" ]] || return 0
  pid="$(<"${file}")"
  if [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null; then
    command_line="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
    if [[ "${command_line}" == *"${expected}"* ]]; then
      # startup2 launches each managed process in its own session; stopping the
      # process group also stops npm/vite and any kubectl helper children.
      kill -- "-${pid}" 2>/dev/null || kill "${pid}" 2>/dev/null || true
      log "Stopped $(basename "${file}" .pid) (${pid})"
    else
      log "Ignored stale PID file $(basename "${file}")"
    fi
  fi
  rm -f "${file}"
}

stop_pid_file "${PID_DIR}/api.pid" "dashboard.backend.main"
stop_pid_file "${PID_DIR}/ui.pid" "npm run dev"
stop_pid_file "${PID_DIR}/remediation-worker.pid" "remediation.worker"

for file in "${PID_DIR}"/pf-*.pid; do
  [[ -e "${file}" ]] || continue
  stop_pid_file "${file}" "port-forward"
done

if [[ "${DELETE_CLUSTER}" == "1" ]]; then
  command -v kind >/dev/null 2>&1 || {
    printf 'ERROR: kind is required to delete cluster %s\n' "${KIND_CLUSTER}" >&2
    exit 1
  }
  if kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER}"; then
    kind delete cluster --name "${KIND_CLUSTER}"
    log "Deleted Kind cluster ${KIND_CLUSTER}"
  fi
elif [[ "${KEEP_CLUSTER_RUNNING}" == "1" ]]; then
  log "Kind cluster ${KIND_CLUSTER} remains running"
else
  command -v docker >/dev/null 2>&1 || {
    printf 'ERROR: docker is required to stop Kind cluster %s\n' "${KIND_CLUSTER}" >&2
    exit 1
  }
  command -v kind >/dev/null 2>&1 || {
    printf 'ERROR: kind is required to discover nodes for cluster %s\n' "${KIND_CLUSTER}" >&2
    exit 1
  }

  node_found=0
  while IFS= read -r node; do
    [[ -n "${node}" ]] || continue
    node_found=1
    if [[ "$(docker inspect -f '{{.State.Running}}' "${node}" 2>/dev/null || true)" == "true" ]]; then
      docker stop "${node}" >/dev/null
      log "Stopped Kind node ${node}; cluster data is preserved"
    else
      log "Kind node ${node} is already stopped"
    fi
  done < <(kind get nodes --name "${KIND_CLUSTER}" 2>/dev/null || true)

  if [[ "${node_found}" == "0" ]]; then
    log "Kind cluster ${KIND_CLUSTER} was not found"
  fi
fi
