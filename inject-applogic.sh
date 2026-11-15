#!/usr/bin/env bash
# inject-applogic.sh — Inject AppLogic faults into otel-demo microservices.
# Turns on demo feature-flag failure paths that live inside service code
# (product-catalog / cart / payment / ad / recommendation / LLM).
#
# Usage:
#   ./inject-applogic.sh              # enable AppLogic faults
#   ./inject-applogic.sh --target payment
#   ./inject-applogic.sh --clear
#   ./inject-applogic.sh --status
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT="${CONTEXT:-kind-gcp-demo-test}"
DEMO_NS="${DEMO_NS:-otel-demo}"
PID_DIR="${ROOT}/.run"
BACKUP="${PID_DIR}/flagd-applogic.backup.json"
ACTION="inject"
TARGET="all"

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --clear|--restore) ACTION="clear"; shift ;;
    --status) ACTION="status"; shift ;;
    --target) TARGET="${2:-all}"; shift 2 ;;
    *) die "unknown arg: $1" ;;
  esac
done

need() { command -v "$1" >/dev/null 2>&1 || die "missing $1"; }
need kubectl
need python3
mkdir -p "${PID_DIR}"

k() { kubectl --context "${CONTEXT}" -n "${DEMO_NS}" "$@"; }

ensure_demo() {
  k get deploy flagd &>/dev/null || die "otel-demo missing. Run ./microservice-startup.sh"
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

plan_for_target() {
  case "${TARGET}" in
    all)
      echo "productCatalogFailure=on"
      echo "cartFailure=on"
      echo "paymentFailure=75%"
      echo "paymentUnreachable=off"
      echo "adFailure=on"
      echo "recommendationCacheFailure=on"
      echo "llmInaccurateResponse=on"
      echo "llmRateLimitError=off"
      ;;
    catalog|product-catalog)
      echo "productCatalogFailure=on"
      ;;
    cart)
      echo "cartFailure=on"
      ;;
    payment)
      echo "paymentFailure=90%"
      echo "paymentUnreachable=off"
      ;;
    ad)
      echo "adFailure=on"
      ;;
    recommendation)
      echo "recommendationCacheFailure=on"
      ;;
    llm)
      echo "llmInaccurateResponse=on"
      echo "llmRateLimitError=on"
      ;;
    *) die "target must be all|catalog|cart|payment|ad|recommendation|llm" ;;
  esac
}

clear_plan() {
  echo "productCatalogFailure=off"
  echo "cartFailure=off"
  echo "paymentFailure=off"
  echo "paymentUnreachable=off"
  echo "adFailure=off"
  echo "recommendationCacheFailure=off"
  echo "llmInaccurateResponse=off"
  echo "llmRateLimitError=off"
}

do_inject() {
  ensure_demo
  if [[ ! -f "${BACKUP}" ]]; then
    get_flagd_json >"${BACKUP}"
    log "Backed up flagd → ${BACKUP}"
  fi
  log "Injecting AppLogic faults (target=${TARGET})"
  mapfile -t plan < <(plan_for_target)
  local new_json
  new_json="$(apply_overrides "${plan[@]}")"
  set_flagd_json "${new_json}"
  # Generate load so failures show up in metrics/traces
  if k get deploy load-generator &>/dev/null; then
    local flood
    flood="$(apply_overrides "loadGeneratorFloodHomepage=on")"
    set_flagd_json "${flood}"
    log "Enabled loadGeneratorFloodHomepage to amplify signal"
  fi
  log "AppLogic faults active. Hit the storefront / wait for alerts, then propose fix in Causa Remediation."
  log "Clear with: ./inject-applogic.sh --clear"
}

do_clear() {
  ensure_demo
  if [[ -f "${BACKUP}" ]]; then
    log "Restoring flagd from backup"
    set_flagd_json "$(cat "${BACKUP}")"
  else
    log "No backup — turning AppLogic flags off"
    mapfile -t plan < <(clear_plan)
    local new_json
    new_json="$(apply_overrides "${plan[@]}" "loadGeneratorFloodHomepage=off")"
    set_flagd_json "${new_json}"
  fi
  log "AppLogic faults cleared"
}

do_status() {
  ensure_demo
  log "AppLogic-related flagd defaults:"
  python3 -c '
import json,sys
doc=json.loads(sys.argv[1])
keys=["productCatalogFailure","cartFailure","paymentFailure","paymentUnreachable","adFailure","recommendationCacheFailure","llmInaccurateResponse","llmRateLimitError","loadGeneratorFloodHomepage"]
for k in keys:
  body=doc.get("flags",{}).get(k)
  if not body: continue
  v=body.get("defaultVariant","?")
  mark=" *" if str(v).lower()!="off" else ""
  print(f"  {k} = {v}{mark}")
' "$(get_flagd_json)"
}

case "${ACTION}" in
  inject) do_inject ;;
  clear) do_clear ;;
  status) do_status ;;
esac
