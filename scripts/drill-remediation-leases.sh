#!/usr/bin/env bash
set -euo pipefail

namespace="${CAUSA_POSTGRES_NAMESPACE:-postgres}"
workload="${CAUSA_POSTGRES_WORKLOAD:-deployment/postgres}"
database="${PGDATABASE:-causa}"
user="${PGUSER:-causa}"
drill_id="lease-drill-$(date +%s)-$$"
worker_a_log="$(mktemp)"
worker_b_log="$(mktemp)"

psql_exec() {
  kubectl exec -n "$namespace" "$workload" -- \
    psql -U "$user" -d "$database" -v ON_ERROR_STOP=1 -P pager=off "$@"
}

cleanup() {
  psql_exec -c "DELETE FROM remediation_events WHERE plan_id='$drill_id'" >/dev/null 2>&1 || true
  psql_exec -c "DELETE FROM remediation_plans WHERE id='$drill_id'" >/dev/null 2>&1 || true
  rm -f -- "$worker_a_log" "$worker_b_log"
}
trap cleanup EXIT

psql_exec -c "
  INSERT INTO remediation_plans
    (id, incident_id, status, created_at)
  VALUES
    ('$drill_id', '$drill_id', 'approved', clock_timestamp())
" >/dev/null

# Worker A holds the candidate row lock while worker B executes the same
# SKIP LOCKED claim query in a separate database session.
psql_exec -c "
  BEGIN;
  SELECT id FROM remediation_plans
   WHERE id='$drill_id' AND status='approved'
   FOR UPDATE SKIP LOCKED;
  SELECT pg_sleep(1);
  UPDATE remediation_plans
     SET status='claimed', claimed_by='worker-a',
         claimed_at=clock_timestamp(),
         lease_until=clock_timestamp()+interval '5 minutes'
   WHERE id='$drill_id';
  COMMIT;
" >"$worker_a_log" &
worker_a_pid=$!

sleep 0.2
psql_exec -c "
  WITH candidate AS (
    SELECT id FROM remediation_plans
     WHERE id='$drill_id' AND status='approved'
     FOR UPDATE SKIP LOCKED
  )
  UPDATE remediation_plans p
     SET status='claimed', claimed_by='worker-b'
    FROM candidate
   WHERE p.id=candidate.id
  RETURNING p.id;
" >"$worker_b_log"
wait "$worker_a_pid"

if ! rg -q '\(0 rows\)' "$worker_b_log"; then
  echo "FAIL: worker-b claimed a row locked by worker-a" >&2
  exit 1
fi

psql_exec -c "
  UPDATE remediation_plans
     SET lease_until=clock_timestamp()-interval '1 second'
   WHERE id='$drill_id' AND claimed_by='worker-a';
  UPDATE remediation_plans
     SET claimed_by='worker-b', claimed_at=clock_timestamp(),
         lease_until=clock_timestamp()+interval '5 minutes'
   WHERE id='$drill_id' AND lease_until < clock_timestamp()
   RETURNING claimed_by;
" >"$worker_b_log"

if ! rg -q 'worker-b' "$worker_b_log"; then
  echo "FAIL: worker-b did not reclaim the expired lease" >&2
  exit 1
fi

psql_exec -c "
  UPDATE remediation_plans
     SET status='succeeded'
   WHERE id='$drill_id' AND claimed_by='worker-a'
     AND lease_until > clock_timestamp()
   RETURNING id;
" >"$worker_a_log"

if ! rg -q '\(0 rows\)' "$worker_a_log"; then
  echo "FAIL: stale worker-a completed worker-b's plan" >&2
  exit 1
fi

echo "PASS: concurrent claim, expiry takeover, and stale-owner rejection"
