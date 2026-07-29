#!/usr/bin/env bash
# recapture-baselines.sh — clear chaos flags, settle, capture clean 60-min baselines.
# Requires: Kind demo up, port-forwards (Prom :9090, Tempo :3200, Loki :3100, PG :15432).
# After helm changes to Prometheus/Alertmanager rules, restart those pods so rules reload.
#
# Usage:
#   ./scripts/recapture-baselines.sh
#   SETTLE_SEC=60 ./scripts/recapture-baselines.sh   # shorter settle for dry runs
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETTLE_SEC="${SETTLE_SEC:-300}"
WINDOW_MIN="${WINDOW_MIN:-60}"
ID="${ROOT}/incident-detection"

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

cd "$ROOT"
[[ -x ./inject-applogic.sh ]] || die "missing inject-applogic.sh"
[[ -x ./inject-issues.sh ]] || die "missing inject-issues.sh"

log "Clearing AppLogic + infra chaos flags"
./inject-applogic.sh --clear
./inject-issues.sh --clear

log "Verify flags are off (inject --status should show cleared)"
./inject-applogic.sh --status || true
./inject-issues.sh --status || true

log "Settling ${SETTLE_SEC}s for metrics to quiet (override with SETTLE_SEC=)"
sleep "$SETTLE_SEC"

export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-15432}"
export PGDATABASE="${PGDATABASE:-causa}"
export PGUSER="${PGUSER:-causa}"
export PGPASSWORD="${PGPASSWORD:-causa}"

cd "$ID"
# shellcheck disable=SC1091
[[ -f .venv/bin/activate ]] && source .venv/bin/activate

log "Capturing ${WINDOW_MIN}-min healthy baselines → baselines/otel-demo.json (+ DB)"
PYTHONPATH=. python -m testbed.harness.capture_baseline \
  --namespace otel-demo \
  --window-minutes "$WINDOW_MIN" \
  --out baselines/otel-demo.json \
  --write-db \
  --prometheus-url http://127.0.0.1:9090 \
  --tempo-url http://127.0.0.1:3200 \
  --loki-url http://127.0.0.1:3100

log "Done. Re-check chaos flags are still off before injecting faults."
log "If you changed Prometheus alert rules, helm upgrade / restart prometheus+alertmanager."
