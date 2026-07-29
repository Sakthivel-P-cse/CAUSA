# Security policy

## Supported use

Causa currently supports controlled development and evaluation deployments.
Production operators must keep automatic remediation disabled until the active
RCA model and calibration artifact meet the documented readiness gates.

## Required configuration

- `CAUSA_API_REQUIRE_AUTH=1`
- unique high-entropy viewer, proposer and approver bearer tokens
- non-empty `TRIGGER_AUTH_TOKEN` on both ingest and Stage-2
- a separate `causa_remediator` database role
- `CAUSA_REMEDIATION_AUTO_PROPOSE=0`
- `CAUSA_REMEDIATION_AUTO_APPLY=0`
- immutable container image tags or digests

Production startup intentionally fails when API authentication or remediation
Postgres persistence is unavailable. The JSON remediation store is permitted
only for explicit single-process development via
`CAUSA_REMEDIATION_ALLOW_FILE_STORE=1`.

## Reporting vulnerabilities

Report vulnerabilities privately to the repository owners. Include affected
version or commit, reproduction steps, impact and any suggested mitigation. Do
not include real credentials, customer telemetry or exploit a live cluster.

## Token rotation

Tokens are supplied through Kubernetes Secrets or local environment variables.
Never put approval tokens in `VITE_*` or other frontend build variables; the
dashboard accepts operator bearer tokens only at runtime and stores them in the
browser session.
Rotate a compromised token, restart affected workloads and inspect
`remediation_events` for unauthorized proposals or approvals.
