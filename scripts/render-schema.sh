#!/usr/bin/env bash
# render-schema.sh — print the Causa Postgres schema to stdout.
#
# The schema lives in the postgres chart's migrate Job (a post-install hook), so
# the DDL that CI and local throwaway databases load is the SAME text the cluster
# applies. Rendering it here rather than keeping a checked-in copy is deliberate:
# a second copy is a copy that drifts, and one already has — helm/postgres's
# templates/configmap.yaml is missing remediation_plans/remediation_events.
#
# Usage:
#   ./scripts/render-schema.sh | psql "$DSN" -v ON_ERROR_STOP=1 -f -
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART="${ROOT}/incident-detection/helm/postgres"

command -v helm >/dev/null 2>&1 || { echo "missing required tool: helm" >&2; exit 1; }

helm template causa-schema "${CHART}" \
  --set auth.password=render-only \
  --set env.PGPASSWORD=render-only \
  -s templates/schema-migrate-job.yaml \
| python3 -c '
import re, sys, yaml

doc = yaml.safe_load(sys.stdin.read())
container = doc["spec"]["template"]["spec"]["containers"][0]
script = "\n".join(container.get("args") or container.get("command") or [])

m = re.search(r"<<\x27SQL\x27\n(.*?)\nSQL", script, re.S)
if not m:
    sys.exit("could not locate the SQL heredoc in the migrate Job")

sql = m.group(1)
# Kubernetes expands $$ -> $ in container command/args before the shell sees
# them, so the template writes $$$$ to land a literal $$ (the PL/pgSQL DO-block
# delimiter). Applying the DDL outside Kubernetes means doing that expansion
# ourselves, or every DO block is a syntax error.
sys.stdout.write(re.sub(r"\$\$", "$", sql))
'
