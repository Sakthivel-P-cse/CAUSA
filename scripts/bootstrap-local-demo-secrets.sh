#!/usr/bin/env bash
set -euo pipefail

# Local Kind only. Credentials are random, live only in Kubernetes Secrets, and
# are intentionally not suitable for production secret management.
for namespace in causa-dashboard causa-remediation; do
  kubectl create namespace "$namespace" --dry-run=client -o yaml |
    kubectl apply -f - >/dev/null
done

if ! kubectl get secret causa-dashboard-auth -n causa-dashboard >/dev/null 2>&1; then
  demo_viewer_token="$(openssl rand -hex 32)"
  demo_proposer_token="$(openssl rand -hex 32)"
  demo_approval_token="$(openssl rand -hex 32)"
  demo_executor_token="$(openssl rand -hex 32)"
  kubectl create secret generic causa-dashboard-auth \
    -n causa-dashboard \
    --from-literal=PGPASSWORD=rca_engine \
    --from-literal=REMEDIATION_PGPASSWORD=causa_remediator_dev \
    --from-literal=CAUSA_API_VIEWER_TOKEN="$demo_viewer_token" \
    --from-literal=CAUSA_REMEDIATION_PROPOSER_TOKEN="$demo_proposer_token" \
    --from-literal=REMEDIATION_APPROVAL_TOKEN="$demo_approval_token" \
    --from-literal=CAUSA_REMEDIATION_EXECUTOR_TOKEN="$demo_executor_token" \
    --dry-run=client -o yaml |
    kubectl apply -f - >/dev/null
fi

if ! kubectl get secret causa-remediation-worker-auth -n causa-remediation >/dev/null 2>&1; then
  kubectl create secret generic causa-remediation-worker-auth \
    -n causa-remediation \
    --from-literal=PGPASSWORD=causa_remediator_dev \
    --dry-run=client -o yaml |
    kubectl apply -f - >/dev/null
fi

printf 'Local demo Secrets are present. Values were not printed.\n'
printf 'Retrieve the dashboard viewer token only when needed:\n'
printf '%s\n' "kubectl get secret causa-dashboard-auth -n causa-dashboard -o jsonpath='{.data.CAUSA_API_VIEWER_TOKEN}' | base64 -d"
