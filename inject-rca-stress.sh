#!/usr/bin/env bash
# inject-rca-stress.sh — Targeted severe faults for RCA engine validation.
#
# Combines flagd AppLogic faults with kubectl dependency/resource stress.
# Each scenario isolates or amplifies a fault class the RCA engine should rank.
#
# Usage:
#   ./inject-rca-stress.sh --scenario critical     # max severity, multi-layer
#   ./inject-rca-stress.sh --scenario payment       # checkout cascade
#   ./inject-rca-stress.sh --scenario catalog       # S0 AppLogic
#   ./inject-rca-stress.sh --scenario kafka         # shared dependency
#   ./inject-rca-stress.sh --scenario valkey        # cache resource pressure
#   ./inject-rca-stress.sh --scenario email-oom     # memory leak + OOM
#   ./inject-rca-stress.sh --scenario checkout      # cart + payment + catalog
#   ./inject-rca-stress.sh --list
#   ./inject-rca-stress.sh --clear
#   ./inject-rca-stress.sh --status
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT="${CONTEXT:-kind-gcp-demo-test}"
DEMO_NS="${DEMO_NS:-${CAUSA_APP_NAMESPACE:-otel-demo}}"
PID_DIR="${ROOT}/.run"
BACKUP="${PID_DIR}/flagd-rca-stress.backup.json"
STATE="${PID_DIR}/rca-stress.state"
ACTION="inject"
SCENARIO="critical"

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage ;;
    --clear|--restore) ACTION="clear"; shift ;;
    --status) ACTION="status"; shift ;;
    --list) ACTION="list"; shift ;;
    --scenario) SCENARIO="${2:-}"; shift 2 ;;
    *) die "unknown arg: $1" ;;
  esac
done

need() { command -v "$1" >/dev/null 2>&1 || die "missing $1"; }
need kubectl
need python3
mkdir -p "${PID_DIR}"

k() { kubectl --context "${CONTEXT}" -n "${DEMO_NS}" "$@"; }

ensure_demo() {
  k get deploy flagd &>/dev/null || die "target stack missing in ${DEMO_NS}. Run ./microservice-startup.sh"
  k get cm flagd-config &>/dev/null || die "flagd-config missing"
}

get_flagd_json() {
  k get cm flagd-config -o jsonpath='{.data.demo\.flagd\.json}'
}

set_flagd_json() {
  local json="$1"
  local tmp payload
  tmp="$(mktemp)"
  printf '%s' "${json}" >"${tmp}"
  payload="$(python3 - "${tmp}" <<'PY'
import json, sys
print(json.dumps({"data": {"demo.flagd.json": open(sys.argv[1]).read()}}))
PY
)"
  rm -f "${tmp}"
  k patch cm flagd-config --type merge -p "${payload}" >/dev/null
}

all_off_plan() {
  cat <<'EOF'
productCatalogFailure=off
cartFailure=off
paymentFailure=off
paymentUnreachable=off
adFailure=off
adHighCpu=off
adManualGc=off
recommendationCacheFailure=off
kafkaQueueProblems=off
loadGeneratorFloodHomepage=off
imageSlowLoad=off
emailMemoryLeak=off
llmRateLimitError=off
llmInaccurateResponse=off
failedReadinessProbe=off
EOF
}

apply_overrides() {
  local current
  current="$(get_flagd_json)"
  python3 - "${current}" "$@" <<'PY'
import json, sys
doc = json.loads(sys.argv[1])
flags = doc.setdefault("flags", {})
for pair in sys.argv[2:]:
    name, _, variant = pair.partition("=")
    if name not in flags:
        print(f"warn: unknown flag {name}", file=sys.stderr)
        continue
    flags[name]["defaultVariant"] = variant
    print(f"  {name} → {variant}", file=sys.stderr)
print(json.dumps(doc, indent=2))
PY
}

scenario_flags() {
  case "${SCENARIO}" in
    catalog)
      echo "productCatalogFailure=on"
      echo "loadGeneratorFloodHomepage=on"
      ;;
    cart)
      echo "cartFailure=on"
      echo "failedReadinessProbe=on"
      echo "loadGeneratorFloodHomepage=on"
      ;;
    payment)
      echo "paymentFailure=100%"
      echo "paymentUnreachable=on"
      echo "cartFailure=on"
      echo "loadGeneratorFloodHomepage=on"
      ;;
    checkout)
      echo "productCatalogFailure=on"
      echo "cartFailure=on"
      echo "paymentFailure=100%"
      echo "paymentUnreachable=on"
      echo "loadGeneratorFloodHomepage=on"
      ;;
    kafka)
      echo "kafkaQueueProblems=on"
      echo "loadGeneratorFloodHomepage=on"
      ;;
    recommendation)
      echo "recommendationCacheFailure=on"
      echo "loadGeneratorFloodHomepage=on"
      ;;
    ad)
      echo "adFailure=on"
      echo "adHighCpu=on"
      echo "adManualGc=on"
      echo "loadGeneratorFloodHomepage=on"
      ;;
    email-oom)
      echo "emailMemoryLeak=10000x"
      echo "loadGeneratorFloodHomepage=on"
      ;;
    valkey)
      echo "cartFailure=on"
      echo "loadGeneratorFloodHomepage=on"
      ;;
  critical)
      echo "productCatalogFailure=on"
      echo "cartFailure=on"
      echo "paymentFailure=100%"
      echo "paymentUnreachable=on"
      echo "adFailure=on"
      echo "adHighCpu=on"
      echo "adManualGc=on"
      echo "recommendationCacheFailure=on"
      echo "kafkaQueueProblems=on"
      echo "emailMemoryLeak=10000x"
      echo "llmRateLimitError=on"
      echo "llmInaccurateResponse=on"
      echo "failedReadinessProbe=on"
      echo "imageSlowLoad=10sec"
      echo "loadGeneratorFloodHomepage=on"
      ;;
    *)
      die "unknown scenario: ${SCENARIO} (try --list)"
      ;;
  esac
}

record_state() {
  local key="$1"
  local val="$2"
  echo "${key}=${val}" >>"${STATE}"
}

inject_kubectl() {
  : >"${STATE}"
  echo "scenario=${SCENARIO}" >>"${STATE}"
  echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"${STATE}"

  kill_pods() {
    local dep="$1"
    if k get deploy "${dep}" &>/dev/null; then
      k delete pod -l "app.kubernetes.io/name=${dep}" --force --grace-period=0 >/dev/null 2>&1 \
        || k delete pod -l "app.kubernetes.io/component=${dep}" --force --grace-period=0 >/dev/null 2>&1 \
        || true
      record_state "killed" "${dep}"
      log "  killed pods: ${dep}"
    fi
  }

  tighten_mem() {
    local dep="$1"
    local limit="$2"
    if k get deploy "${dep}" &>/dev/null; then
      local before
      before="$(k get deploy "${dep}" -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}' 2>/dev/null || echo "")"
      record_state "mem_before_${dep}" "${before}"
      k set resources "deploy/${dep}" --limits="memory=${limit}" >/dev/null 2>&1 || true
      record_state "mem_patched" "${dep}:${limit}"
      log "  ${dep} memory limit → ${limit}"
    fi
  }

  scale_down() {
    local dep="$1"
    if k get deploy "${dep}" &>/dev/null; then
      local replicas
      replicas="$(k get deploy "${dep}" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 1)"
      record_state "replicas_before_${dep}" "${replicas}"
      k scale "deploy/${dep}" --replicas=0 >/dev/null 2>&1 || true
      record_state "scaled_down" "${dep}"
      log "  scaled ${dep} → 0"
    fi
  }

  case "${SCENARIO}" in
    catalog)
      kill_pods product-catalog
      ;;
    cart)
      kill_pods cart
      tighten_mem cart 48Mi
      ;;
    payment)
      kill_pods payment
      scale_down payment
      ;;
    checkout)
      kill_pods checkout
      kill_pods payment
      scale_down payment
      ;;
    kafka)
      kill_pods kafka
      ;;
    valkey)
      tighten_mem valkey-cart 32Mi
      kill_pods valkey-cart
      ;;
    email-oom)
      tighten_mem email 32Mi
      kill_pods email
      ;;
    ad)
      tighten_mem ad 48Mi
      kill_pods ad
      ;;
    recommendation)
      kill_pods recommendation
      ;;
    critical)
      for dep in product-catalog cart payment checkout kafka valkey-cart email ad recommendation; do
        kill_pods "${dep}"
      done
      tighten_mem email 32Mi
      tighten_mem ad 48Mi
      tighten_mem valkey-cart 32Mi
      scale_down payment
      (
        sleep 60
        local reps
        reps="$(grep '^replicas_before_payment=' "${STATE}" 2>/dev/null | cut -d= -f2- || echo 1)"
        k scale deploy/payment --replicas="${reps:-1}" >/dev/null 2>&1 || true
        log "  auto-restored payment replicas → ${reps:-1}"
      ) &
      disown
      ;;
  esac
}

drive_traffic() {
  local port base
  port="$(k get svc frontend-proxy -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")"
  for base in ":${port}" ":8080" ":18080"; do
    [[ "${base}" == ":" ]] && continue
    if curl -sf -o /dev/null -m 2 "http://127.0.0.1${base}/" >/dev/null 2>&1; then
      log "Driving checkout traffic via ${base}"
      (
        for _ in $(seq 1 50); do
          curl -sf -o /dev/null -m 2 -X POST "http://127.0.0.1${base}/api/cart" \
            -H 'Content-Type: application/json' \
            -d '{"itemId":"OLJCESPC7Z","quantity":1}' >/dev/null 2>&1 || true
          sleep 0.4
        done
      ) &
      disown
      return 0
    fi
  done
  log "No reachable storefront — skipping traffic drive"
}

do_inject() {
  ensure_demo
  if [[ ! -f "${BACKUP}" ]]; then
    get_flagd_json >"${BACKUP}"
    log "Backed up flagd → ${BACKUP}"
  fi

  log "Scenario: ${SCENARIO}"
  mapfile -t off_plan < <(all_off_plan)
  local base_json
  base_json="$(apply_overrides "${off_plan[@]}")"
  set_flagd_json "${base_json}"

  mapfile -t plan < <(scenario_flags)
  log "Enabling ${#plan[@]} flagd faults"
  local new_json
  new_json="$(apply_overrides "${plan[@]}")"
  set_flagd_json "${new_json}"

  log "Applying kubectl stress"
  inject_kubectl
  drive_traffic

  log "RCA stress active (scenario=${SCENARIO}). Watch alerts + Investigation."
  log "Clear with: ./inject-rca-stress.sh --clear"
}

clear_kubectl() {
  [[ -f "${STATE}" ]] || return 0
  log "Undoing kubectl stress"
  local line key val
  while IFS= read -r line || [[ -n "${line}" ]]; do
    key="${line%%=*}"
    val="${line#*=}"
    case "${key}" in
      mem_patched)
        local dep="${val%%:*}"
        local before
        before="$(grep "^mem_before_${dep}=" "${STATE}" 2>/dev/null | head -1 | cut -d= -f2- || true)"
        if [[ -n "${before}" ]]; then
          k set resources "deploy/${dep}" --limits="memory=${before}" >/dev/null 2>&1 || true
          log "  restored ${dep} memory → ${before}"
        else
          k set resources "deploy/${dep}" --limits="memory=128Mi" >/dev/null 2>&1 || true
        fi
        ;;
      scaled_down)
        local reps
        reps="$(grep "^replicas_before_${val}=" "${STATE}" 2>/dev/null | head -1 | cut -d= -f2- || echo 1)"
        k scale "deploy/${val}" --replicas="${reps}" >/dev/null 2>&1 || true
        log "  restored ${val} replicas → ${reps}"
        ;;
    esac
  done <"${STATE}"
  rm -f "${STATE}"
}

do_clear() {
  ensure_demo
  if [[ -f "${BACKUP}" ]]; then
    log "Restoring flagd from backup"
    set_flagd_json "$(cat "${BACKUP}")"
  else
    mapfile -t off_plan < <(all_off_plan)
    set_flagd_json "$(apply_overrides "${off_plan[@]}")"
  fi
  clear_kubectl
  log "RCA stress cleared"
}

do_status() {
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
  fi
}

do_list() {
  cat <<'EOF'
Scenarios (fault class → expected RCA target):
  catalog        AppLogic     product-catalog
  cart           AppLogic     cart
  payment        AppLogic     payment (+ checkout cascade)
  checkout       AppLogic     checkout path (catalog→cart→payment)
  kafka          Dependency   kafka (shared async backbone)
  valkey         Resource     valkey-cart (cache pressure)
  email-oom      Resource     email (memory leak + OOM)
  ad             Resource     ad (CPU/GC saturation)
  recommendation AppLogic     recommendation
  critical       Multi-layer  severe mix for stress testing
EOF
}

case "${ACTION}" in
  inject) do_inject ;;
  clear) do_clear ;;
  status) do_status ;;
  list) do_list ;;
esac
