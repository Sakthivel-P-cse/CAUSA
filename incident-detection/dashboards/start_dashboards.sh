#!/usr/bin/env bash
# ============================================================
# start_dashboards.sh
# Start all 3 Causa telemetry web dashboards.
#
# Port mapping:
#   Port 5001 — Logs     (from Loki        in namespace loki        :3100)
#   Port 5002 — Metrics  (from Prometheus  in namespace prometheus  :9090)
#   Port 5003 — Traces   (from Tempo       in namespace tempo        :3200)
#
# This script also sets up the required kubectl port-forwards
# for Loki, Prometheus, and Tempo if they are not already running.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "╔════════════════════════════════════════════════════╗"
echo "║      Causa · Telemetry Web Dashboards          ║"
echo "╚════════════════════════════════════════════════════╝"
echo ""

# ── Kill any stale port-forwards ──────────────────────────
for port in 3100 9090 3200 5001 5002 5003; do
  pid=$(lsof -ti tcp:"${port}" 2>/dev/null || true)
  if [[ -n "${pid}" ]]; then
    echo "  Stopping existing process on port ${port} (PID ${pid})..."
    kill "${pid}" 2>/dev/null || true
    sleep 0.3
  fi
done

# ── Kill any stale dashboard processes ────────────────────
for port in 5001 5002 5003; do
  pid=$(lsof -ti tcp:"${port}" 2>/dev/null || true)
  if [[ -n "${pid}" ]]; then
    echo "  Stopping existing dashboard on port ${port} (PID ${pid})..."
    kill "${pid}" 2>/dev/null || true
    sleep 0.3
  fi
done

echo ""
echo "  Setting up kubectl port-forwards..."

# Port-forward Loki  (logs → namespace loki)
kubectl -n loki port-forward svc/loki 3100:3100 &>/dev/null &
echo "    ✓  Loki       → localhost:3100  (kubectl -n loki port-forward svc/loki)"

# Port-forward Prometheus (metrics → namespace prometheus)
kubectl -n prometheus port-forward svc/prometheus 9090:9090 &>/dev/null &
echo "    ✓  Prometheus → localhost:9090  (kubectl -n prometheus port-forward svc/prometheus)"

# Port-forward Tempo (traces → namespace tempo)
kubectl -n tempo port-forward svc/tempo 3200:3200 &>/dev/null &
echo "    ✓  Tempo      → localhost:3200  (kubectl -n tempo port-forward svc/tempo)"

sleep 2
echo ""
echo "  Starting dashboards..."
echo ""

python3 "${SCRIPT_DIR}/logs_dashboard.py"    &
LOGS_PID=$!
sleep 0.4

python3 "${SCRIPT_DIR}/metrics_dashboard.py" &
METRICS_PID=$!
sleep 0.4

python3 "${SCRIPT_DIR}/traces_dashboard.py"  &
TRACES_PID=$!
sleep 0.4

echo ""
echo "┌─────────────────────────────────────────────────────┐"
echo "│  Dashboards running:                                │"
echo "│                                                     │"
echo "│  📋  Logs     →  http://localhost:5001              │"
echo "│  📊  Metrics  →  http://localhost:5002              │"
echo "│  🔍  Traces   →  http://localhost:5003              │"
echo "│                                                     │"
echo "│  All pages auto-refresh every 10 seconds.          │"
echo "│  Press Ctrl+C to stop everything.                  │"
echo "└─────────────────────────────────────────────────────┘"
echo ""

# Trap Ctrl+C and kill all children
cleanup() {
  echo ""
  echo "Stopping all dashboards and port-forwards..."
  kill "${LOGS_PID}" "${METRICS_PID}" "${TRACES_PID}" 2>/dev/null || true
  pkill -f "kubectl.*port-forward" 2>/dev/null || true
  echo "Done."
  exit 0
}
trap cleanup SIGINT SIGTERM

wait "${LOGS_PID}" "${METRICS_PID}" "${TRACES_PID}"
