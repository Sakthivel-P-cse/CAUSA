#!/usr/bin/env bash
# inject-issues.sh — Inject many faults into the otel-demo microservices
# so Causa (Watchdog / RCA) has rich signal to detect and explain.
#
# Uses flagd feature flags (no Chaos Mesh required) plus optional kubectl
# stress (pod kills, memory pressure, restarts).
#
# Usage:
#   ./inject-issues.sh                  # medium intensity, leave faults on
#   ./inject-issues.sh --level heavy
#   ./inject-issues.sh --level light --hold 300   # auto-clear after 5m
#   ./inject-issues.sh --clear          # restore flagd + undo kubectl stress
#   ./inject-issues.sh --status
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT="${CONTEXT:-kind-gcp-demo-test}"
DEMO_NS="${DEMO_NS:-otel-demo}"
PID_DIR="${ROOT}/.run"
BACKUP="${PID_DIR}/flagd-config.backup.json"
STATE="${PID_DIR}/inject-issues.state"
LEVEL="medium"
HOLD_SECONDS=0
ACTION="inject"
KUBECTL_STRESS=1

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing required tool: $1"; }

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage ;;
    --clear|--restore) ACTION="clear"; shift ;;
    --status) ACTION="status"; shift ;;
    --level) LEVEL="${2:-}"; shift 2 ;;
    --hold) HOLD_SECONDS="${2:-0}"; shift 2 ;;
    --no-kubectl) KUBECTL_STRESS=0; shift ;;
    *) die "unknown arg: $1 (try --help)" ;;
  esac
done

case "${LEVEL}" in
  light|medium|heavy) ;;
  *) die "level must be light|medium|heavy (got: ${LEVEL})" ;;
esac

need kubectl
need python3
mkdir -p "${PID_DIR}"

k() { kubectl --context "${CONTEXT}" -n "${DEMO_NS}" "$@"; }

ensure_demo() {
  k get deploy flagd &>/dev/null || die "otel-demo not found in ${DEMO_NS}. Run ./microservice-startup.sh first."
  k get cm flagd-config &>/dev/null || die "flagd-config ConfigMap missing in ${DEMO_NS}"
}

get_flagd_json() {
  k get cm flagd-config -o jsonpath='{.data.demo\.flagd\.json}'
}

set_flagd_json() {
  local json="$1"
  local payload tmp
  tmp="$(mktemp)"
  printf '%s' "${json}" >"${tmp}"
  payload="$(python3 - "${tmp}" <<'PY'
import json, sys
flags = open(sys.argv[1]).read()
print(json.dumps({"data": {"demo.flagd.json": flags}}))
PY
)"
  rm -f "${tmp}"
  k patch cm flagd-config --type merge -p "${payload}" >/dev/null
}

backup_flagd() {
  if [[ ! -f "${BACKUP}" ]]; then
    get_flagd_json >"${BACKUP}"
    log "Backed up flagd config → ${BACKUP}"
  else
    log "Keeping existing backup ${BACKUP}"
  fi
}

# Apply desired defaultVariant overrides. Args are FLAG=VARIANT pairs.
apply_flag_overrides() {
  local overrides=("$@")
  local current
  current="$(get_flagd_json)"
  python3 - "${current}" "${overrides[@]}" <<'PY'
import json, sys
doc = json.loads(sys.argv[1])
flags = doc.setdefault("flags", {})
for pair in sys.argv[2:]:
    name, _, variant = pair.partition("=")
    if name not in flags:
        print(f"warn: unknown flag {name}", file=sys.stderr)
        continue
    flags[name]["defaultVariant"] = variant
    print(f"  flag {name} → {variant}", file=sys.stderr)
print(json.dumps(doc, indent=2))
PY
}

flag_plan() {
  case "${LEVEL}" in
    light)
      echo "productCatalogFailure=on"
      echo "recommendationCacheFailure=on"
      echo "adFailure=on"
      echo "paymentFailure=25%"
      echo "loadGeneratorFloodHomepage=on"
      ;;
    medium)
      echo "productCatalogFailure=on"
      echo "recommendationCacheFailure=on"
      echo "adFailure=on"
      echo "adHighCpu=on"
      echo "cartFailure=on"
      echo "paymentFailure=50%"
      echo "paymentUnreachable=on"
      echo "kafkaQueueProblems=on"
      echo "loadGeneratorFloodHomepage=on"
      echo "imageSlowLoad=5sec"
      echo "emailMemoryLeak=10x"
      echo "llmRateLimitError=on"
      ;;
    heavy)
      echo "productCatalogFailure=on"
      echo "recommendationCacheFailure=on"
      echo "adFailure=on"
      echo "adHighCpu=on"
      echo "adManualGc=on"
      echo "cartFailure=on"
      echo "paymentFailure=90%"
      echo "paymentUnreachable=on"
      echo "kafkaQueueProblems=on"
      echo "loadGeneratorFloodHomepage=on"
      echo "imageSlowLoad=10sec"
      echo "emailMemoryLeak=1000x"
      echo "llmRateLimitError=on"
      echo "llmInaccurateResponse=on"
      echo "failedReadinessProbe=on"
      ;;
  esac
}

inject_flagd() {
  backup_flagd
  log "Enabling flagd faults (level=${LEVEL})"
  mapfile -t plan < <(flag_plan)
  local new_json
  new_json="$(apply_flag_overrides "${plan[@]}")"
  set_flagd_json "${new_json}"
  log "flagd faults applied (${#plan[@]} flags)"
}

# Kubectl-side stress: pod kills + tight memory limits (OOM pressure).
# State recorded so --clear can undo.
inject_kubectl_stress() {
  [[ "${KUBECTL_STRESS}" -eq 1 ]] || { log "Skipping kubectl stress (--no-kubectl)"; return 0; }

  local targets=(
    product-catalog
    payment
    cart
    recommendation
    ad
    email
    checkout
    currency
  )

  : >"${STATE}"
  echo "level=${LEVEL}" >>"${STATE}"
  echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"${STATE}"

  log "Deleting pods to force restarts / gaps"
  local d
  for d in "${targets[@]}"; do
    if k get deploy "${d}" &>/dev/null; then
      k delete pod -l "app.kubernetes.io/name=${d}" --force --grace-period=0 >/dev/null 2>&1 \
        || k delete pod -l "app.kubernetes.io/component=${d}" --force --grace-period=0 >/dev/null 2>&1 \
        || true
      echo "killed=${d}" >>"${STATE}"
    fi
  done

  if [[ "${LEVEL}" == "medium" || "${LEVEL}" == "heavy" ]]; then
    log "Tightening memory limits (OOM / restart pressure)"
    local mem="64Mi"
    [[ "${LEVEL}" == "heavy" ]] && mem="48Mi"
    for d in email ad recommendation; do
      if k get deploy "${d}" &>/dev/null; then
        local before
        before="$(k get deploy "${d}" -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}' 2>/dev/null || echo "")"
        echo "mem_before_${d}=${before}" >>"${STATE}"
        k set resources "deploy/${d}" --limits="memory=${mem}" >/dev/null 2>&1 || true
        echo "mem_patched=${d}:${mem}" >>"${STATE}"
        log "  ${d} memory limit → ${mem}"
      fi
    done
  fi

  if [[ "${LEVEL}" == "heavy" ]]; then
    log "Scaling payment briefly to amplify checkout failures"
    local replicas
    replicas="$(k get deploy payment -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 1)"
    echo "payment_replicas_before=${replicas}" >>"${STATE}"
    k scale deploy/payment --replicas=0 >/dev/null 2>&1 || true
    echo "payment_scaled=0" >>"${STATE}"
    (
      sleep 45
      k scale deploy/payment --replicas="${replicas:-1}" >/dev/null 2>&1 || true
    ) &
    disown
    log "  payment scaled to 0 (auto-restore in ~45s)"
  fi

  log "Kubectl stress recorded in ${STATE}"
}

clear_flagd() {
  if [[ -f "${BACKUP}" ]]; then
    log "Restoring flagd from ${BACKUP}"
    set_flagd_json "$(cat "${BACKUP}")"
  else
    log "No backup found — turning known failure flags off"
    local offs=(
      productCatalogFailure=off
      recommendationCacheFailure=off
      adFailure=off
      adHighCpu=off
      adManualGc=off
      cartFailure=off
      paymentFailure=off
      paymentUnreachable=off
      kafkaQueueProblems=off
      loadGeneratorFloodHomepage=off
      imageSlowLoad=off
      emailMemoryLeak=off
      llmRateLimitError=off
      llmInaccurateResponse=off
      failedReadinessProbe=off
    )
    local new_json
    new_json="$(apply_flag_overrides "${offs[@]}")"
    set_flagd_json "${new_json}"
  fi
}

clear_kubectl_stress() {
  [[ -f "${STATE}" ]] || { log "No kubectl stress state file"; return 0; }

  log "Undoing kubectl stress from ${STATE}"
  local line key val
  while IFS= read -r line || [[ -n "${line}" ]]; do
    key="${line%%=*}"
    val="${line#*=}"
    case "${key}" in
      mem_patched)
        local dep="${val%%:*}"
        local before=""
        before="$(grep "^mem_before_${dep}=" "${STATE}" 2>/dev/null | head -1 | cut -d= -f2- || true)"
        if [[ -n "${before}" ]]; then
          k set resources "deploy/${dep}" --limits="memory=${before}" >/dev/null 2>&1 || true
          log "  restored ${dep} memory → ${before}"
        else
          k set resources "deploy/${dep}" --limits="memory=128Mi" >/dev/null 2>&1 || true
          log "  restored ${dep} memory → 128Mi (fallback)"
        fi
        ;;
      payment_replicas_before)
        k scale deploy/payment --replicas="${val}" >/dev/null 2>&1 || true
        log "  restored payment replicas → ${val}"
        ;;
    esac
  done <"${STATE}"

  rm -f "${STATE}"
}

show_status() {
  ensure_demo
  log "Active flagd defaults (non-off):"
  python3 -c '
import json, sys
doc = json.loads(sys.argv[1])
for name, body in sorted(doc.get("flags", {}).items()):
    v = body.get("defaultVariant", "?")
    if str(v).lower() != "off":
        print(f"  {name} = {v}")
' "$(get_flagd_json)"
  if [[ -f "${STATE}" ]]; then
    log "Kubectl stress state:"
    sed 's/^/  /' "${STATE}"
  else
    log "No kubectl stress state on disk"
  fi
  if [[ -f "${BACKUP}" ]]; then
    log "flagd backup present: ${BACKUP}"
  fi
}

do_inject() {
  ensure_demo
  inject_flagd
  inject_kubectl_stress
  log "Injection complete (level=${LEVEL}). Watch Causa Overview / Investigation."
  if [[ "${HOLD_SECONDS}" -gt 0 ]]; then
    log "Holding for ${HOLD_SECONDS}s then auto-clearing…"
    sleep "${HOLD_SECONDS}"
    clear_flagd
    clear_kubectl_stress
    log "Auto-clear done"
  else
    log "Leave running, or clear later with: ./inject-issues.sh --clear"
  fi
}

do_clear() {
  ensure_demo
  clear_flagd
  clear_kubectl_stress
  log "All injected issues cleared"
}

case "${ACTION}" in
  inject) do_inject ;;
  clear) do_clear ;;
  status) show_status ;;
esac
