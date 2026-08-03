#!/usr/bin/env bash
set -euo pipefail
CONTEXT="${CONTEXT:-kind-gcp-demo-test}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if ! kubectl --context "${CONTEXT}" get crd constrainttemplates.templates.gatekeeper.sh &>/dev/null; then
  echo "Gatekeeper CRDs missing — installing Gatekeeper"
  kubectl --context "${CONTEXT}" apply -f https://raw.githubusercontent.com/open-policy-agent/gatekeeper/v3.17.1/deploy/gatekeeper.yaml
  kubectl --context "${CONTEXT}" -n gatekeeper-system rollout status deploy/gatekeeper-controller-manager --timeout=180s || true
fi
# Split apply: template first, wait for CRD, then constraint.
#
# NOTE: an earlier awk-based split lived here. It passed TMP as an *awk*
# variable (`TMP="$TMP"`) while reading it via ENVIRON["TMP"], which is only
# populated from the real environment — so ENVIRON["TMP"] was empty and awk
# wrote its halves to ./.t and ./.c in the caller's working directory. Both got
# committed to the repo root. The python split below already did the real work,
# so the awk pass was pure side effect and is gone.
TMP="$(mktemp)"
trap 'rm -f "${TMP}" "${TMP}.t" "${TMP}.c"' EXIT
python3 - <<PY
from pathlib import Path
text = Path("${ROOT}/remediation/helm/gatekeeper-remediation/constraint.yaml").read_text()
parts = [p.strip() for p in text.split("---") if p.strip()]
Path("${TMP}.t").write_text(parts[0] + "\n")
Path("${TMP}.c").write_text(parts[1] + "\n" if len(parts) > 1 else "")
PY
kubectl --context "${CONTEXT}" apply -f "${TMP}.t"
echo "Waiting for CausaRemediationAllowlist CRD"
for i in $(seq 1 60); do
  if kubectl --context "${CONTEXT}" get crd causaremediationallowlists.constraints.gatekeeper.sh &>/dev/null; then
    break
  fi
  sleep 2
done
kubectl --context "${CONTEXT}" apply -f "${TMP}.c"
echo "Gatekeeper remediation constraints applied"
