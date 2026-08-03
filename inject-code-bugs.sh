#!/usr/bin/env bash
# inject-code-bugs.sh — Inject REAL application code bugs into otel-demo services
# (not flagd feature-flag triggers). Builds/patches images and rolls them out.
#
# Bugs planted (see demo-bugs/):
#   payment          TypeError — wrong field amount.nano (JS)
#   recommendation   IndexError — off-by-one in get_product_list (Python)
#   product-catalog  nil pointer panic on GetProduct (Go)
#   checkout         integer divide-by-zero in PlaceOrder (Go)
#   cart             NullReferenceException on GetCart (.NET)
#
# Usage:
#   ./inject-code-bugs.sh                  # all services
#   ./inject-code-bugs.sh --services payment,recommendation
#   ./inject-code-bugs.sh --clear          # restore stock images
#   ./inject-code-bugs.sh --status
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_SRC="${DEMO_SRC:-/home/bhuvi/PROJECTS/opentelemetry-demo}"
BUGS="${ROOT}/testbed/demo-bugs"
CONTEXT="${CONTEXT:-kind-gcp-demo-test}"
KIND_CLUSTER="${KIND_CLUSTER:-gcp-demo-test}"
DEMO_NS="${DEMO_NS:-${CAUSA_APP_NAMESPACE:-otel-demo}}"
STOCK_TAG="${STOCK_TAG:-ghcr.io/open-telemetry/demo:2.2.0}"
ACTION="inject"
SERVICES="payment,recommendation,product-catalog,checkout,cart"
STATE="${ROOT}/.run/code-bugs.state"

log() { printf '==> %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing $1"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --clear|--restore) ACTION="clear"; shift ;;
    --status) ACTION="status"; shift ;;
    --services) SERVICES="${2:-}"; shift 2 ;;
    *) die "unknown arg: $1" ;;
  esac
done

need kubectl
need docker
need kind
mkdir -p "${ROOT}/.run"

k() { kubectl --context "${CONTEXT}" -n "${DEMO_NS}" "$@"; }

ensure_demo() {
  k get deploy payment &>/dev/null || die "target microservice stack missing in ${DEMO_NS}. Run ./microservice-startup.sh"
  [[ -d "${BUGS}" ]] || die "missing ${BUGS} — bug source patches not found"
  [[ -d "${DEMO_SRC}" ]] || die "missing DEMO_SRC=${DEMO_SRC}"
}

has_svc() {
  [[ ",${SERVICES}," == *",$1,"* ]]
}

patch_interpreted_image() {
  local svc="$1" stock="$2" dest="$3" src_file="$4"
  local cid tag
  tag="causa/buggy-${svc}:local"
  log "Patching ${svc} image (copy ${src_file} → ${dest})"
  cid="$(docker create "${stock}")"
  docker cp "${src_file}" "${cid}:${dest}"
  docker commit -m "causa real code bug: ${svc}" "${cid}" "${tag}" >/dev/null
  docker rm "${cid}" >/dev/null
  echo "${tag}"
}

build_go_service() {
  local svc="$1" dockerfile="$2" buggy_main="$3"
  local tag bak
  tag="causa/buggy-${svc}:local"
  bak="$(mktemp)"
  log "Building ${svc} with patched main.go"
  cp "${DEMO_SRC}/src/${svc}/main.go" "${bak}"
  cp "${buggy_main}" "${DEMO_SRC}/src/${svc}/main.go"
  (
    cd "${DEMO_SRC}"
    docker build -f "${dockerfile}" -t "${tag}" .
  )
  mv "${bak}" "${DEMO_SRC}/src/${svc}/main.go"
  echo "${tag}"
}

build_cart() {
  local tag bak
  tag="causa/buggy-cart:local"
  bak="$(mktemp)"
  log "Building cart with patched CartService.cs (.NET — may take a few minutes)"
  cp "${DEMO_SRC}/src/cart/src/services/CartService.cs" "${bak}"
  cp "${BUGS}/cart/CartService.cs" "${DEMO_SRC}/src/cart/src/services/CartService.cs"
  (
    cd "${DEMO_SRC}"
    docker build -f src/cart/src/Dockerfile -t "${tag}" .
  )
  mv "${bak}" "${DEMO_SRC}/src/cart/src/services/CartService.cs"
  echo "${tag}"
}

load_and_roll() {
  local svc="$1" tag="$2"
  log "Loading ${tag} into kind (${KIND_CLUSTER})"
  kind load docker-image "${tag}" --name "${KIND_CLUSTER}"
  local cname
  cname="$(k get deploy "${svc}" -o jsonpath='{.spec.template.spec.containers[0].name}')"
  log "Rolling ${svc} → ${tag}"
  k set image "deploy/${svc}" "${cname}=${tag}"
  k patch "deploy/${svc}" --type strategic -p \
    "{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"${cname}\",\"imagePullPolicy\":\"Never\"}]}}}}" \
    >/dev/null
  echo "${svc}=${tag}" >>"${STATE}"
  k rollout status "deploy/${svc}" --timeout=180s || true
}

restore_stock() {
  local svc="$1"
  local tag="${STOCK_TAG}-${svc}"
  local cname
  cname="$(k get deploy "${svc}" -o jsonpath='{.spec.template.spec.containers[0].name}' 2>/dev/null || true)"
  [[ -n "${cname}" ]] || return 0
  log "Restoring ${svc} → ${tag}"
  k set image "deploy/${svc}" "${cname}=${tag}"
  k patch "deploy/${svc}" --type strategic -p \
    "{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"${cname}\",\"imagePullPolicy\":\"IfNotPresent\"}]}}}}" \
    >/dev/null || true
  k rollout status "deploy/${svc}" --timeout=180s || true
}

clear_flagd() {
  if [[ -x "${ROOT}/inject-applogic.sh" ]]; then
    "${ROOT}/inject-applogic.sh" --clear >/dev/null 2>&1 || true
  fi
  if [[ -x "${ROOT}/inject-issues.sh" ]]; then
    "${ROOT}/inject-issues.sh" --clear >/dev/null 2>&1 || true
  fi
  if [[ -x "${ROOT}/inject-rca-stress.sh" ]]; then
    "${ROOT}/inject-rca-stress.sh" --clear >/dev/null 2>&1 || true
  fi
}

do_inject() {
  ensure_demo
  clear_flagd
  : >"${STATE}"
  echo "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"${STATE}"
  echo "services=${SERVICES}" >>"${STATE}"

  local tag
  if has_svc payment; then
    tag="$(patch_interpreted_image payment "${STOCK_TAG}-payment" /usr/src/app/charge.js "${BUGS}/payment/charge.js")"
    load_and_roll payment "${tag}"
  fi
  if has_svc recommendation; then
    tag="$(patch_interpreted_image recommendation "${STOCK_TAG}-recommendation" /app/recommendation_server.py "${BUGS}/recommendation/recommendation_server.py")"
    load_and_roll recommendation "${tag}"
  fi
  if has_svc product-catalog; then
    tag="$(build_go_service product-catalog src/product-catalog/Dockerfile "${BUGS}/product-catalog/main.go")"
    load_and_roll product-catalog "${tag}"
  fi
  if has_svc checkout; then
    tag="$(build_go_service checkout src/checkout/Dockerfile "${BUGS}/checkout/main.go")"
    load_and_roll checkout "${tag}"
  fi
  if has_svc cart; then
    tag="$(build_cart)"
    load_and_roll cart "${tag}"
  fi

  log "Code bugs deployed. Drive storefront traffic, then check Causa Investigation."
  log "Bug manifest:"
  cat <<'EOF'
  payment         → TypeError (amount.nano typo)
  recommendation  → IndexError (off-by-one)
  product-catalog → nil pointer panic (GetProduct)
  checkout        → divide-by-zero (PlaceOrder)
  cart            → NullReferenceException (GetCart)
EOF
  log "Clear with: ./inject-code-bugs.sh --clear"
}

do_clear() {
  ensure_demo
  local svcs=(payment recommendation product-catalog checkout cart)
  if [[ -f "${STATE}" ]]; then
    local line
    while IFS= read -r line || [[ -n "${line}" ]]; do
      case "${line}" in
        *=causa/buggy-*) svcs+=("${line%%=*}") ;;
      esac
    done <"${STATE}"
  fi
  local seen="" s
  for s in "${svcs[@]}"; do
    [[ " ${seen} " == *" ${s} "* ]] && continue
    seen+=" ${s}"
    restore_stock "${s}"
  done
  rm -f "${STATE}"
  log "Stock images restored"
}

do_status() {
  ensure_demo
  log "Deployed images:"
  local s
  for s in payment recommendation product-catalog checkout cart; do
    local img
    img="$(k get deploy "${s}" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo '?')"
    printf '  %-18s %s\n' "${s}" "${img}"
  done
  if [[ -f "${STATE}" ]]; then
    log "State file:"
    sed 's/^/  /' "${STATE}"
  fi
  log "Bug sources in ${BUGS}"
}

case "${ACTION}" in
  inject) do_inject ;;
  clear) do_clear ;;
  status) do_status ;;
esac
