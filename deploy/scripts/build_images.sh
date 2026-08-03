#!/usr/bin/env bash
# build_images.sh — Build causa/ingest + causa/stage2-rca and optionally kind-load.
#
# Usage:
#   ./scripts/build_images.sh [--kind-cluster <name>] [--no-load] [--ingest-only|--stage2-only]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

KIND_CLUSTER="gcp-demo-test"
DO_LOAD=1
BUILD_INGEST=1
BUILD_STAGE2=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kind-cluster) KIND_CLUSTER="${2:?}"; shift 2 ;;
    --no-load) DO_LOAD=0; shift ;;
    --ingest-only) BUILD_STAGE2=0; shift ;;
    --stage2-only) BUILD_INGEST=0; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if ! command -v docker &>/dev/null; then
  echo "ERROR: docker not found"
  exit 1
fi

IMAGES=()

if [[ "${BUILD_INGEST}" -eq 1 ]]; then
  echo "Building causa/ingest:latest"
  docker build \
    -f "${ROOT_DIR}/incidents/Dockerfile" \
    -t causa/ingest:latest \
    "${ROOT_DIR}"
  IMAGES+=(causa/ingest:latest)
fi

if [[ "${BUILD_STAGE2}" -eq 1 ]]; then
  echo "Building causa/stage2-rca:latest"
  docker build \
    -f "${ROOT_DIR}/rca/Dockerfile" \
    -t causa/stage2-rca:latest \
    "${ROOT_DIR}"
  IMAGES+=(causa/stage2-rca:latest)
fi

if [[ "${DO_LOAD}" -eq 1 ]]; then
  if ! command -v kind &>/dev/null; then
    echo "ERROR: kind not found (pass --no-load to skip)"
    exit 1
  fi
  for img in "${IMAGES[@]}"; do
    echo "kind load ${img} → ${KIND_CLUSTER}"
    kind load docker-image "${img}" --name "${KIND_CLUSTER}"
  done
fi

echo "Built: ${IMAGES[*]}"
