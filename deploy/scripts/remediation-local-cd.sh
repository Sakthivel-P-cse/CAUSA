#!/usr/bin/env bash
set -euo pipefail

SERVICE="${1:-}"
IMAGE_TAG="${2:-local-$(date +%Y%m%d%H%M%S)}"
NS="${DEMO_NS:-otel-demo}"
KIND_CLUSTER="${KIND_CLUSTER:-kind-gcp-demo-test}"
REGISTRY_PREFIX="${REGISTRY_PREFIX:-ghcr.io/${GITHUB_REPOSITORY_OWNER:-bhuvi-ship-it}}"

if [[ -z "${SERVICE}" ]]; then
  echo "usage: $0 <service> [image-tag]"
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

if [[ -f "testbed/demo-bugs/${SERVICE}/Dockerfile" ]]; then
  BUILD_CTX="testbed/demo-bugs/${SERVICE}"
elif [[ -f "src/${SERVICE}/Dockerfile" ]]; then
  BUILD_CTX="src/${SERVICE}"
else
  echo "no Dockerfile found for service=${SERVICE}"
  exit 1
fi

IMAGE="${REGISTRY_PREFIX}/causa-${SERVICE}:${IMAGE_TAG}"
docker build -t "${IMAGE}" "${BUILD_CTX}"
kind load docker-image "${IMAGE}" --name "${KIND_CLUSTER#kind-}"

if kubectl --context "${KIND_CLUSTER}" -n "${NS}" get rollout "${SERVICE}" >/dev/null 2>&1; then
  kubectl --context "${KIND_CLUSTER}" -n "${NS}" set image "rollout/${SERVICE}" "${SERVICE}=${IMAGE}"
  kubectl --context "${KIND_CLUSTER}" -n "${NS}" rollout status "rollout/${SERVICE}" --timeout=10m
else
  kubectl --context "${KIND_CLUSTER}" -n "${NS}" set image "deploy/${SERVICE}" "${SERVICE}=${IMAGE}"
  kubectl --context "${KIND_CLUSTER}" -n "${NS}" rollout status "deploy/${SERVICE}" --timeout=10m
fi

echo "deployed ${SERVICE} image=${IMAGE}"
