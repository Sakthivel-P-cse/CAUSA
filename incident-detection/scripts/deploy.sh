#!/usr/bin/env bash
# ============================================================
# deploy.sh — Deploy the full Causa observability stack
#
# Usage:
#   ./scripts/deploy.sh [dev|prod]
#
# Deploys all components in dependency order:
#   1.  Namespaces
#   2.  kube-state-metrics (K8s state metrics)
#   3.  node-exporter      (node hardware metrics)
#   4.  MinIO (Tempo)      (S3 object storage for Tempo trace blocks)
#   5.  Tempo              (trace backend → writes blocks to minio-tempo)
#   6.  MinIO (Loki)       (S3 object storage for Loki chunk blocks)
#   7.  Loki               (log backend → writes chunks to minio-loki)
#   8.  MinIO (Mimir)      (S3 object storage for Mimir metric blocks)
#   9.  Mimir              (long-term metric storage → remote_write target)
#  10.  Prometheus         (metrics scrape+alert engine → remote_write Mimir)
#  11.  OTel Collector     (ingestion GATEWAY — tail sampling + export)
#  12.  OTel LB             (trace-ID load balancer → correct gateway pod)
#  13.  OTel Agent          (per-node DaemonSet — collect + forward)
#  14.  Alertmanager       (alert routing → Stage 2 webhook)
#  15.  Postgres           (incident + ground-truth + RCA report storage)
#  16.  Stage-2 mock       (DEV ONLY — trivial receiver so the trigger forward
#                           is testable; Dev B replaces it with the real engine)
#  17.  Causa Ingest       (Dev-A trigger: opens window/state, forwards to Stage 2)
#
# NOT deployed here (other lanes): the real stage2-rca engine (Dev B) and the
# causa-dashboard UI (Dev C). See helm/postgres/DB_ROLE_CONTRACT.md.
#
# Deploy order: each MinIO must be up before the backend that uses it.
#   minio-tempo → tempo   (Tempo writes trace blocks to minio-tempo)
#   minio-loki  → loki    (Loki writes chunk blocks to minio-loki)
#   minio-mimir → mimir   (Mimir writes metric blocks to minio-mimir)
# Prometheus comes after Mimir so the remote_write target exists at startup.
#
# Ingestion is three-tier (Datadog/OTel gateway pattern + LB for sampling):
#   pods → OTel Agent (DaemonSet)
#        → traces:        OTel LB (route by trace ID)
#        → metrics/logs:  OTel Collector (gateway) directly
#        → OTel Collector (gateway: tail sampling + export) → backends
# Deploy order matters: the gateway is up before the LB (the LB's
# resolver discovers gateway pods), and both are up before the agents
# (the agents' forward targets must exist when they start).
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
HELM_DIR="${ROOT_DIR}/helm"
MANIFESTS_DIR="${ROOT_DIR}/manifests"

ENV="${1:-dev}"

if [[ "${ENV}" != "dev" && "${ENV}" != "prod" ]]; then
  echo "ERROR: environment must be 'dev' or 'prod'. Got: '${ENV}'"
  echo "Usage: $0 [dev|prod]"
  exit 1
fi

VALUES_FILE="values-${ENV}.yaml"

# Storage class used for PersistentVolumeClaims in prod.
# Override at runtime:  STORAGE_CLASS=my-class ./scripts/deploy.sh prod
STORAGE_CLASS="${STORAGE_CLASS:-standard}"

echo "========================================================"
echo "  Causa Observability Stack — Deploying [${ENV^^}]"
echo "========================================================"
echo ""

# ── Step 0: Validate tools ─────────────────────────────────
for tool in kubectl helm; do
  if ! command -v "${tool}" &>/dev/null; then
    echo "ERROR: '${tool}' is not installed or not in PATH."
    exit 1
  fi
done

echo "kubectl version: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"
echo "helm version:    $(helm version --short)"
echo ""

# ── Step 0b: Verify cluster reachability ───────────────────
echo "Checking Kubernetes cluster connectivity..."
if ! kubectl cluster-info --request-timeout=5s &>/dev/null; then
  echo ""
  echo "ERROR: Cannot reach a Kubernetes cluster."
  echo "  • Check that a cluster is running (minikube, kind, a cloud cluster, etc.)"
  echo "  • Verify ~/.kube/config points to the correct context:"
  echo "      kubectl config get-contexts"
  echo "      kubectl config use-context <name>"
  echo "  • Test manually: kubectl cluster-info"
  exit 1
fi
echo "  ✓ Cluster is reachable: $(kubectl config current-context)"
echo ""

# ── Step 0c: Production pre-flight checks ─────────────────
if [[ "${ENV}" == "prod" ]]; then
  echo "Running production pre-flight checks..."

  # Postgres password secret must exist before the chart is deployed.
  # Create it with:
  #   kubectl create namespace postgres --dry-run=client -o yaml | kubectl apply -f -
  #   kubectl -n postgres create secret generic postgres-secret \
  #     --from-literal=postgres-password='<STRONG_PASSWORD>'
  if ! kubectl -n postgres get secret postgres-secret &>/dev/null; then
    echo ""
    echo "ERROR: Production secret 'postgres-secret' not found in namespace 'postgres'."
    echo "  Create it before deploying prod:"
    echo ""
    echo "    kubectl create namespace postgres --dry-run=client -o yaml | kubectl apply -f -"
    echo "    kubectl -n postgres create secret generic postgres-secret \\"
    echo "      --from-literal=postgres-password='<STRONG_PASSWORD>'"
    echo ""
    exit 1
  fi
  echo "  ✓ postgres-secret exists"

  # MinIO (for Mimir) root credentials — the S3 access/secret key.
  if ! kubectl -n minio-mimir get secret minio-mimir-secret &>/dev/null; then
    echo ""
    echo "ERROR: Production secret 'minio-mimir-secret' not found in namespace 'minio-mimir'."
    echo "  kubectl create namespace minio-mimir --dry-run=client -o yaml | kubectl apply -f -"
    echo "  kubectl -n minio-mimir create secret generic minio-mimir-secret \\"
    echo "    --from-literal=root-user='<ACCESS_KEY>' \\"
    echo "    --from-literal=root-password='<SECRET_KEY>'"
    exit 1
  fi
  echo "  ✓ minio-mimir-secret exists"

  # Mimir S3 credentials — MUST match the minio-mimir root creds above.
  if ! kubectl -n mimir get secret mimir-s3-secret &>/dev/null; then
    echo ""
    echo "ERROR: Production secret 'mimir-s3-secret' not found in namespace 'mimir'."
    echo "  Use the SAME values as minio-mimir-secret:"
    echo "  kubectl create namespace mimir --dry-run=client -o yaml | kubectl apply -f -"
    echo "  kubectl -n mimir create secret generic mimir-s3-secret \\"
    echo "    --from-literal=root-user='<ACCESS_KEY>' \\"
    echo "    --from-literal=root-password='<SECRET_KEY>'"
    exit 1
  fi
  echo "  ✓ mimir-s3-secret exists"

  # Tempo S3 credentials — MUST match the minio-tempo root creds.
  if ! kubectl -n tempo get secret tempo-s3-secret &>/dev/null; then
    echo ""
    echo "ERROR: Production secret 'tempo-s3-secret' not found in namespace 'tempo'."
    echo "  Use the SAME values as minio-tempo-secret:"
    echo "  kubectl create namespace tempo --dry-run=client -o yaml | kubectl apply -f -"
    echo "  kubectl -n tempo create secret generic tempo-s3-secret \\"
    echo "    --from-literal=root-user='<ACCESS_KEY>' \\"
    echo "    --from-literal=root-password='<SECRET_KEY>'"
    exit 1
  fi
  echo "  ✓ tempo-s3-secret exists"

  # MinIO (for Tempo) root credentials.
  if ! kubectl -n minio-tempo get secret minio-tempo-secret &>/dev/null; then
    echo ""
    echo "ERROR: Production secret 'minio-tempo-secret' not found in namespace 'minio-tempo'."
    echo "  kubectl create namespace minio-tempo --dry-run=client -o yaml | kubectl apply -f -"
    echo "  kubectl -n minio-tempo create secret generic minio-tempo-secret \\"
    echo "    --from-literal=root-user='<ACCESS_KEY>' \\"
    echo "    --from-literal=root-password='<SECRET_KEY>'"
    exit 1
  fi
  echo "  ✓ minio-tempo-secret exists"

  # Loki S3 credentials — MUST match the minio-loki root creds.
  if ! kubectl -n loki get secret loki-s3-secret &>/dev/null; then
    echo ""
    echo "ERROR: Production secret 'loki-s3-secret' not found in namespace 'loki'."
    echo "  Use the SAME values as minio-loki-secret:"
    echo "  kubectl create namespace loki --dry-run=client -o yaml | kubectl apply -f -"
    echo "  kubectl -n loki create secret generic loki-s3-secret \\"
    echo "    --from-literal=root-user='<ACCESS_KEY>' \\"
    echo "    --from-literal=root-password='<SECRET_KEY>'"
    exit 1
  fi
  echo "  ✓ loki-s3-secret exists"

  # MinIO (for Loki) root credentials.
  if ! kubectl -n minio-loki get secret minio-loki-secret &>/dev/null; then
    echo ""
    echo "ERROR: Production secret 'minio-loki-secret' not found in namespace 'minio-loki'."
    echo "  kubectl create namespace minio-loki --dry-run=client -o yaml | kubectl apply -f -"
    echo "  kubectl -n minio-loki create secret generic minio-loki-secret \\"
    echo "    --from-literal=root-user='<ACCESS_KEY>' \\"
    echo "    --from-literal=root-password='<SECRET_KEY>'"
    exit 1
  fi
  echo "  ✓ minio-loki-secret exists"

  # NOTE: stage2-rca (Dev B) and causa-dashboard (Dev C) are NOT deployed by this
  # script — they own those charts + their secrets. See helm/postgres/DB_ROLE_CONTRACT.md.

  if ! kubectl -n causa-ingest get secret causa-ingest-secret &>/dev/null; then
    echo ""
    echo "ERROR: Production secret 'causa-ingest-secret' not found in namespace 'causa-ingest'."
    echo "  The ingest trigger writes incident + ground-truth state as the postgres"
    echo "  'causa' user; without this secret every write fails silently."
    echo "  kubectl create namespace causa-ingest --dry-run=client -o yaml | kubectl apply -f -"
    echo "  kubectl -n causa-ingest create secret generic causa-ingest-secret \\"
    echo "    --from-literal=PGPASSWORD='<SAME_AS_POSTGRES_CAUSA_PASSWORD>'"
    exit 1
  fi
  echo "  ✓ causa-ingest-secret exists"

  # Verify the storage class exists on this cluster
  if ! kubectl get storageclass "${STORAGE_CLASS}" &>/dev/null; then
    echo ""
    echo "ERROR: StorageClass '${STORAGE_CLASS}' not found on this cluster."
    echo "  Available storage classes:"
    kubectl get storageclass --no-headers 2>/dev/null | awk '{print "    -", $1}'
    echo ""
    echo "  Re-run with the correct class name:"
    echo "    STORAGE_CLASS=<name> ./scripts/deploy.sh prod"
    exit 1
  fi
  echo "  ✓ StorageClass '${STORAGE_CLASS}' exists"
  echo ""
fi

# ── Step 1: Apply Namespaces ───────────────────────────────
echo "──────────────────────────────────────────────────────"
echo "[1/17] Applying namespaces..."
echo "──────────────────────────────────────────────────────"
kubectl apply -f "${MANIFESTS_DIR}/namespaces.yaml"
echo ""

# Helper function for Helm install/upgrade
helm_deploy() {
  local release="${1}"
  local chart_dir="${HELM_DIR}/${release}"
  local namespace="${2}"
  local step="${3}"
  local total="${4}"

  echo "──────────────────────────────────────────────────────"
  echo "[${step}/${total}] Deploying ${release} → namespace: ${namespace}"
  echo "──────────────────────────────────────────────────────"

  helm lint "${chart_dir}" \
    -f "${chart_dir}/values.yaml" \
    -f "${chart_dir}/${VALUES_FILE}" \
    --quiet

  # In prod, forward the (possibly overridden) storage class into each chart
  local extra_args=()
  if [[ "${ENV}" == "prod" ]]; then
    extra_args+=(--set "persistence.storageClassName=${STORAGE_CLASS}")
  fi

  helm upgrade --install "${release}" "${chart_dir}" \
    --namespace "${namespace}" \
    --create-namespace \
    -f "${chart_dir}/values.yaml" \
    -f "${chart_dir}/${VALUES_FILE}" \
    "${extra_args[@]}" \
    --wait \
    --timeout 5m

  echo "✓ ${release} deployed"
  echo ""
}

# Total component count for the progress display: dev includes the stage2 mock.
if [[ "${ENV}" == "dev" ]]; then TOTAL=17; else TOTAL=16; fi

# ── Deploy components (in dependency order) ────────────────────
helm_deploy "kube-state-metrics" "kube-state-metrics" 2  "${TOTAL}"
helm_deploy "node-exporter"      "node-exporter"      3  "${TOTAL}"
helm_deploy "minio-tempo"        "minio-tempo"        4  "${TOTAL}"
helm_deploy "tempo"              "tempo"              5  "${TOTAL}"
helm_deploy "minio-loki"         "minio-loki"         6  "${TOTAL}"
helm_deploy "loki"               "loki"               7  "${TOTAL}"
helm_deploy "minio-mimir"        "minio-mimir"        8  "${TOTAL}"
helm_deploy "mimir"              "mimir"              9  "${TOTAL}"
helm_deploy "prometheus"         "prometheus"         10 "${TOTAL}"
helm_deploy "otel-collector"     "otel-collector"     11 "${TOTAL}"
helm_deploy "otel-lb"            "otel-lb"            12 "${TOTAL}"
helm_deploy "otel-agent"         "otel-agent"         13 "${TOTAL}"
helm_deploy "alertmanager"       "alertmanager"       14 "${TOTAL}"
helm_deploy "postgres"           "postgres"           15 "${TOTAL}"

# Dev B's real RCA engine and Dev C's dashboard are deployed by THEM, not here
# (out of the Dev-A lane). In DEV we stand up a trivial mock receiver so the
# trigger's forward path + Alertmanager routing are testable end-to-end; it
# provides the `stage2-rca` service, which Dev B's chart swaps in cleanly. In
# PROD no mock runs — the trigger buffers forwards to stage2_outbox until Dev B's
# engine is reachable. See helm/postgres/DB_ROLE_CONTRACT.md.
if [[ "${ENV}" == "dev" ]]; then
  helm_deploy "stage2-mock"      "stage2-rca"         16 "${TOTAL}"
  helm_deploy "causa-ingest"     "causa-ingest"       17 "${TOTAL}"
else
  helm_deploy "causa-ingest"     "causa-ingest"       16 "${TOTAL}"
fi

# ── Summary ────────────────────────────────────────────────
echo "========================================================"
echo "  All components deployed successfully! [${ENV^^}]"
echo "========================================================"
echo ""
  echo "  Quick access (port-forwards):"
  echo "    Prometheus:      kubectl -n prometheus port-forward svc/prometheus 9090:9090"
  echo "    Tempo:           kubectl -n tempo port-forward svc/tempo 3200:3200"
  echo "    Loki:            kubectl -n loki port-forward svc/loki 3100:3100"
  echo "    Mimir:           kubectl -n mimir port-forward svc/mimir 8080:8080"
  echo "    MinIO-Tempo:     kubectl -n minio-tempo port-forward svc/minio-tempo 9001:9001  (console)"
  echo "    MinIO-Loki:      kubectl -n minio-loki port-forward svc/minio-loki 9002:9001   (console)"
  echo "    MinIO-Mimir:     kubectl -n minio-mimir port-forward svc/minio-mimir 9003:9001  (console)"
  echo "    Alertmgr:        kubectl -n alertmanager port-forward svc/alertmanager 9093:9093"
  echo "    Postgres:        kubectl -n postgres port-forward svc/postgres 5432:5432"
  echo "    Ingest trigger:  kubectl -n causa-ingest port-forward svc/causa-ingest 8080:8080"
  echo "                     → POST http://localhost:8080/trigger"
  if [[ "${ENV}" == "dev" ]]; then
  echo "    Stage-2 (mock):  kubectl -n stage2-rca port-forward svc/stage2-rca 8081:8080  (dev placeholder)"
  fi
echo ""
echo "  Run validation:"
echo "    ./scripts/validate.sh"
echo ""
