#!/usr/bin/env bash
set -euo pipefail
CONTEXT="${CONTEXT:-kind-gcp-demo-test}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if ! kubectl --context "${CONTEXT}" get crd constrainttemplates.templates.gatekeeper.sh &>/dev/null; then
  echo "Gatekeeper CRDs missing — installing Gatekeeper"
  kubectl --context "${CONTEXT}" apply -f https://raw.githubusercontent.com/open-policy-agent/gatekeeper/v3.17.1/deploy/gatekeeper.yaml
  kubectl --context "${CONTEXT}" -n gatekeeper-system rollout status deploy/gatekeeper-controller-manager --timeout=180s || true
fi
# Split apply: template first, wait for CRD, then constraint
TMP="$(mktemp)"
awk 'BEGIN{p=1} /^---$/{if(p==1){p=2; next}} {if(p==1) print > ENVIRON["TMP"]".t"; else print > ENVIRON["TMP"]".c"}' TMP="$TMP" \
  "${ROOT}/incident-detection/helm/gatekeeper-remediation/constraint.yaml" || true
# simpler split with csplit/python
python3 - <<PY
from pathlib import Path
text = Path("${ROOT}/incident-detection/helm/gatekeeper-remediation/constraint.yaml").read_text()
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
rm -f "${TMP}.t" "${TMP}.c"
echo "Gatekeeper remediation constraints applied"
