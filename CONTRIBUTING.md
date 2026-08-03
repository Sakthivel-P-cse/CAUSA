# Contributing

Keep changes within the typed contracts between ingestion, RCA and remediation.
New remediation actions must provide an exact target, reversible change,
idempotency key, expiry, SLO gate and least-privilege capability mapping.

Before submitting a change, run:

```bash
PYTHONPATH=.import:. .venv/bin/pytest -q
cd ../dashboard/frontend && npm run lint && npm test && npm run build
cd ../.. && bash scripts/check-prom-rules.sh
```

Tests described as unit tests must not require Kubernetes, Postgres or network
access. Put live-cluster tests behind an explicit marker and mock the boundary
in unit and contract tests.
