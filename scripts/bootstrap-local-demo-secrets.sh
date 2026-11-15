#!/usr/bin/env bash
set -euo pipefail

# Local Kind only. Database credentials live in Kubernetes Secrets and are
# intentionally not suitable for production secret management.
for namespace in causa-dashboard causa-remediation; do
  kubectl create namespace "$namespace" --dry-run=client -o yaml |
    kubectl apply -f - >/dev/null
done

if ! kubectl get secret causa-dashboard-auth -n causa-dashboard >/dev/null 2>&1; then
  kubectl create secret generic causa-dashboard-auth \
    -n causa-dashboard \
    --from-literal=PGPASSWORD=rca_engine \
    --from-literal=REMEDIATION_PGPASSWORD=causa_remediator_dev \
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
