# Security policy

## Supported use

Causa currently supports controlled development and evaluation deployments.
Production operators must keep automatic remediation disabled until the active
RCA model and calibration artifact meet the documented readiness gates.

## Required configuration

- a separate `causa_remediator` database role
- `CAUSA_REMEDIATION_AUTO_PROPOSE=0`
- `CAUSA_REMEDIATION_AUTO_APPLY=0`
- immutable container image tags or digests

Causa's dashboard API, Alertmanager webhook, ingest trigger and Stage-2 trigger
do not perform application-level authentication. Keep every service on a
trusted private network and do not expose their ports through a public ingress.
If a deployment needs untrusted network access, place an authenticating reverse
proxy or service mesh policy in front of it.

Production startup intentionally fails when remediation Postgres persistence is
unavailable. The JSON remediation store is permitted only for explicit
single-process development via `CAUSA_REMEDIATION_ALLOW_FILE_STORE=1`.

## Reporting vulnerabilities

Report vulnerabilities privately to the repository owners. Include affected
version or commit, reproduction steps, impact and any suggested mitigation. Do
not include real credentials, customer telemetry or exploit a live cluster.

External integration credentials, including NVIDIA NIM and GitHub tokens,
remain secrets and must be rotated through their respective providers if
compromised.
