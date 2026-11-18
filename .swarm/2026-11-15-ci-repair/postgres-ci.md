# Postgres CI failure

- GitHub run `43737906279`, step **Load Causa schema into CI Postgres**, failed first with `ModuleNotFoundError: No module named 'yaml'` in `scripts/render-schema.sh`'s Python parser.
- The script has `set -euo pipefail`; that failed producer stops the pipeline before `psql` receives any DDL. CI then continued to pytest because the workflow does not enable step-level fail-fast across steps, and `test_on_conflict_clause_parses_and_is_idempotent` failed on its first `DELETE` because `rca_reports` had never been created. The test cleanup error was secondary.
- Root cause: CI installed `incident-detection/requirements-devc.txt`, which declared pytest but omitted the PyYAML dependency required by `render-schema.sh`.
- Fix: added `PyYAML>=6.0` to `incident-detection/requirements-devc.txt`; no test workaround or skip was added.
- Checks: `./scripts/render-schema.sh` completed locally; rendered DDL contains `CREATE TABLE IF NOT EXISTS rca_reports` and `uq_rca_incident_id`; local `import yaml` succeeded (PyYAML 6.0.3).
- Limitation: no local PostgreSQL server is available (`pg_isready` reports no response), so the real-Postgres test was not reproduced here. The hosted log directly confirms why schema loading failed.
