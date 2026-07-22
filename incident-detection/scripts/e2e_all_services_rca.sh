#!/usr/bin/env bash
# ============================================================
# e2e_all_services_rca.sh — trigger→Stage-2→Postgres LOOP SMOKE
#
# Scales services to zero to fire DeploymentReplicasUnavailable.
# This is NOT an RCA accuracy gate (availability faults often do not
# cross θ_fire). Use chaos catalog + eval.live_runner for accuracy.
# ============================================================
set -euo pipefail

CTX=""
NS=""
TRAFFIC_GEN=""
SVC_FILTER=""
INJECT_MODE="scale-zero"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)
      CTX="$2"
      shift 2
      ;;
    --app-namespace)
      NS="$2"
      shift 2
      ;;
    --traffic-generator-deployment)
      TRAFFIC_GEN="$2"
      shift 2
      ;;
    --service-filter-regex)
      SVC_FILTER="$2"
      shift 2
      ;;
    --inject-mode)
      INJECT_MODE="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 --context <ctx> --app-namespace <ns> [--traffic-generator-deployment <deploy>] [--service-filter-regex <regex>] [--inject-mode <mode>]"
      exit 1
      ;;
  esac
done

if [[ -z "$CTX" || -z "$NS" ]]; then
    echo "Usage: $0 --context <ctx> --app-namespace <ns> [--traffic-generator-deployment <deploy>] [--service-filter-regex <regex>] [--inject-mode <mode>]"
    exit 1
fi

if ! kubectl --context "$CTX" cluster-info --request-timeout=8s >/dev/null 2>&1; then
  echo "ERROR: Kubernetes context '$CTX' is unreachable from this terminal runtime."
  exit 1
fi

# 1) Traffic Generator
if [[ -n "$TRAFFIC_GEN" ]]; then
  echo "Ensuring traffic generator is running..."
  kubectl --context "$CTX" -n "$NS" rollout restart deploy/"$TRAFFIC_GEN" >/dev/null || true
  kubectl --context "$CTX" -n "$NS" rollout status deploy/"$TRAFFIC_GEN" --timeout=180s || true
else
  echo "No traffic generator provided. Skipping traffic prep."
fi

# 2) Build service list dynamically from what's actually deployed
if [[ -n "$SVC_FILTER" ]]; then
  mapfile -t SERVICES < <(
    kubectl --context "$CTX" -n "$NS" get deploy -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
    | grep -E "$SVC_FILTER" \
    | sort
  )
else
  # Default filter out loadgenerator and redis-cart if they happen to be there (demo compatibility)
  mapfile -t SERVICES < <(
    kubectl --context "$CTX" -n "$NS" get deploy -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
    | grep -Ev '^(loadgenerator|redis-cart)$' \
    | sort
  )
fi

echo "Services to test:"
printf ' - %s\n' "${SERVICES[@]}"

# 3) Save original replicas so we can always restore
declare -A ORIG
for s in "${SERVICES[@]}"; do
  ORIG[$s]="$(kubectl --context "$CTX" -n "$NS" get deploy "$s" -o jsonpath='{.spec.replicas}')"
done

restore_all() {
  echo ""
  echo "Restoring all replicas..."
  for s in "${SERVICES[@]}"; do
    kubectl --context "$CTX" -n "$NS" scale deploy "$s" --replicas="${ORIG[$s]}" >/dev/null 2>&1 || true
  done
}
trap restore_all EXIT

# 4) For each service: inject outage -> wait for Stage1 alert + Stage2 RCA -> restore
for s in "${SERVICES[@]}"; do
  echo ""
  echo "============================================================"
  echo "Testing service: $s (Mode: $INJECT_MODE)"
  echo "============================================================"

  if [[ "$INJECT_MODE" == "scale-zero" ]]; then
    kubectl --context "$CTX" -n "$NS" scale deploy "$s" --replicas=0 >/dev/null
    echo "Scaled $s to 0. Waiting 270s for alert+RCA..."
  elif [[ "$INJECT_MODE" == "cpu-stress" ]]; then
    # Future enhancement: apply CPU stress patch
    echo "CPU stress mode not fully wired. Scaling to 0 as fallback."
    kubectl --context "$CTX" -n "$NS" scale deploy "$s" --replicas=0 >/dev/null
  elif [[ "$INJECT_MODE" == "memory-stress" ]]; then
    # Future enhancement: apply Mem stress patch
    echo "Memory stress mode not fully wired. Scaling to 0 as fallback."
    kubectl --context "$CTX" -n "$NS" scale deploy "$s" --replicas=0 >/dev/null
  else
    echo "Unknown inject mode. Scaling to 0."
    kubectl --context "$CTX" -n "$NS" scale deploy "$s" --replicas=0 >/dev/null
  fi
  
  sleep 270

  echo "Latest RCA report for alerted_service='$s':"
  kubectl --context "$CTX" -n postgres exec deployment/postgres -- \
    psql -U causa -d causa -c "
      SELECT id, alert_name, alerted_service, root_cause_service, failure_type,
             ROUND(confidence_score::numeric, 2) AS confidence, created_at
      FROM rca_reports
      WHERE alerted_service = '$s'
      ORDER BY created_at DESC
      LIMIT 1;
    " || echo "No report found or DB unavailable."

  kubectl --context "$CTX" -n "$NS" scale deploy "$s" --replicas="${ORIG[$s]}" >/dev/null
  kubectl --context "$CTX" -n "$NS" rollout status deploy/"$s" --timeout=180s
  sleep 20
done

echo ""
echo "================ FINAL RCA COVERAGE SUMMARY ================"
kubectl --context "$CTX" -n postgres exec deployment/postgres -- \
  psql -U causa -d causa -c "
    SELECT alerted_service, COUNT(*) AS reports
    FROM rca_reports
    WHERE created_at > NOW() - INTERVAL '4 hours'
    GROUP BY alerted_service
    ORDER BY alerted_service;
  " || true
