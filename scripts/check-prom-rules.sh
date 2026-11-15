#!/usr/bin/env bash
# Validate Prometheus rule packs rendered from the Helm chart.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART="${ROOT}/incident-detection/helm/prometheus"
OUT_DIR="${TMPDIR:-/tmp}/causa-prom-rules-$$"
mkdir -p "${OUT_DIR}"
cleanup() { rm -rf "${OUT_DIR}"; }
trap cleanup EXIT

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required tool: $1" >&2
    exit 1
  }
}

need helm
need python3

echo "==> helm template (phase2 enabled)"
helm template causa-prom "${CHART}" \
  -f "${CHART}/values.yaml" \
  --set rules.phase2.enabled=true \
  --set rules.phase2.slo.enabled=true \
  --set rules.phase2.platform.enabled=true \
  --set rules.phase2.dependencies.enabled=true \
  -s templates/rules-configmap.yaml \
  > "${OUT_DIR}/rules-cm.yaml"

python3 - "${OUT_DIR}" <<'PY'
from pathlib import Path
import re
import sys

out = Path(sys.argv[1])
text = (out / "rules-cm.yaml").read_text()
if "Error:" in text[:300]:
    print(text[:800], file=sys.stderr)
    sys.exit(1)

keys = re.findall(r"^  ([a-z0-9.-]+\.yml): \|", text, re.M)
expected = {
    "causa-alerts-phase1.yml",
    "causa-slo-recording.yml",
    "causa-slo-burn.yml",
    "causa-dependencies.yml",
    "causa-platform.yml",
}
missing = expected - set(keys)
if missing:
    print(f"missing rule files: {sorted(missing)}", file=sys.stderr)
    sys.exit(1)

for key in sorted(expected):
    m = re.search(
        rf"^  {re.escape(key)}: \|\n((?:.*\n)+?)(?=^  [a-z0-9.-]+\.yml: \||\Z)",
        text,
        re.M,
    )
    if not m:
        print(f"failed to extract {key}", file=sys.stderr)
        sys.exit(1)
    body = "\n".join(
        line[4:] if line.startswith("    ") else line
        for line in m.group(1).splitlines()
    )
    path = out / key
    path.write_text(body + "\n")
    if "groups:" not in body:
        print(f"{key} missing groups:", file=sys.stderr)
        sys.exit(1)
    print(f"ok {key} ({body.count('- alert:')} alerts, {body.count('- record:')} records)")

required_snippets = [
    ("causa-slo-burn.yml", "SLOAvailabilityFastBurn"),
    ("causa-slo-burn.yml", "route: page"),
    ("causa-slo-recording.yml", "causa:slo_availability:burn_rate_5m"),
    ("causa-dependencies.yml", "DependencyKafkaUnavailable"),
    ("causa-platform.yml", "LokiDown"),
    ("causa-alerts-phase1.yml", "ServiceHighErrorRate"),
]
for fname, needle in required_snippets:
    content = (out / fname).read_text()
    if needle not in content:
        print(f"{fname} missing expected snippet: {needle}", file=sys.stderr)
        sys.exit(1)
print("rule pack content checks passed")
PY

if command -v promtool >/dev/null 2>&1; then
  echo "==> promtool check rules"
  for f in "${OUT_DIR}"/causa-*.yml; do
    promtool check rules "$f"
  done
else
  if [[ "${REQUIRE_PROMTOOL:-0}" == "1" ]]; then
    need docker
    echo "==> promtool check rules (container)"
    for f in "${OUT_DIR}"/causa-*.yml; do
      docker run --rm --entrypoint promtool \
        -v "${OUT_DIR}:/rules:ro" \
        prom/prometheus:v3.5.0 \
        check rules "/rules/$(basename "${f}")"
    done
  else
    echo "==> promtool not installed; skipped syntax check (set REQUIRE_PROMTOOL=1 to require it)"
  fi
fi

echo "==> helm template (phase2 disabled)"
helm template causa-prom "${CHART}" \
  -f "${CHART}/values.yaml" \
  -s templates/rules-configmap.yaml \
  > "${OUT_DIR}/rules-cm-off.yaml"

python3 - "${OUT_DIR}/rules-cm-off.yaml" <<'PY'
from pathlib import Path
import re
import sys
text = Path(sys.argv[1]).read_text()
keys = set(re.findall(r"^  ([a-z0-9.-]+\.yml): \|", text, re.M))
assert keys == {"causa-alerts-phase1.yml"}, f"unexpected keys with phase2 off: {keys}"
print("phase2-off keys ok:", sorted(keys))
PY

echo "OK: Prometheus Phase-2 rule packs validated"
