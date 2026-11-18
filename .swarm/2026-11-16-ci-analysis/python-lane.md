# Python CI lane

## Environment and scope

- Repository sources were copied to `/tmp/causa-python-lane-20261116/incident-detection` and the sibling `dashboard/` copied beside it so test imports resolve. All generated artifacts stayed in that copy; no source or artifact in the user checkout was edited.
- Disposable venv: `/tmp/causa-python-lane-20261116/venv`, Python 3.14.9. CI specifies Python 3.12, so this is a local-version difference.
- Every Python command used `KUBECONFIG=/dev/null`. The parent-provided disposable Postgres was `postgresql://sakthi@127.0.0.1:55439/causa_test`.

## Results

| Workflow step | Result |
| --- | --- |
| `pip install -e '.[dev,trigger]'`; `pip install -r requirements-devc.txt`; `pip install pip-audit` | All succeeded in the venv. Initial sandboxed pip attempt could not resolve `pypi.org`; retry with network access succeeded. |
| `pip-audit` | Exit 0: “No known vulnerabilities found.” It reported local `causa-ingest (0.1.0)` skipped because it is not found on PyPI. |
| `PYTHONPATH=. pytest -q tests` with Postgres DSN | `369 passed, 1 skipped in 4.22s`. The skipped test reason was not collected (`-rs` was not used). |
| Explicit `test_on_conflict_clause_parses_and_is_idempotent` blocker | `1 passed in 0.16s`; CI’s `grep "1 passed"` condition would pass. |
| `PYTHONPATH=. pytest -q tests/test_dev_c.py` | `26 passed in 1.16s`. |
| `PYTHONPATH=. python -m eval.runner` | Exit 0; golden fixture assertions passed; wrote `artifacts/golden_eval.json` in the temp copy. FixtureFull, MostAnomalous, and ABFull top-1 were 1.0. |
| `PYTHONPATH=. python -m remediation.cli --auto-approve` | Exit 0 and emitted the typed plan. During plan generation it attempted `http://localhost:8080`; kubectl reported `socket: operation not permitted` and “Unable to connect to the server.” No Kubernetes connection or operation succeeded. |
| `CAUSA_NIM_ENABLED=0 pytest -q tests/test_nim_client.py tests/test_remediation_service.py` | `14 passed in 0.56s`. |
| `PYTHONPATH=. python -m eval.report` | Exit 0; fixture smoke report generated in the temp copy (`kind=fixture_smoke`, `ece_smoke=0.0`). It logged the same blocked localhost Kubernetes lookup while routing a dependency proposal. |
| `PYTHONPATH=. pytest -q tests/test_live_eval.py` | `4 passed in 0.07s`. |

## Limitations

- The initial full-suite attempt used an incomplete temp copy and stopped at collection with `ModuleNotFoundError: No module named 'dashboard'`. After copying the sibling dashboard package into the temp checkout, the workflow command completed as reported above; this was a harness-copy issue, not a source failure.
- Although `KUBECONFIG=/dev/null` was set as requested, the CLI/report path falls back to kubectl’s localhost default. Sandbox socket restrictions blocked the requests. Treat those two demo-related commands as output-successful with a blocked Kubernetes lookup, not as clean no-Kubernetes runs.
- The one suite skip reason remains unknown. Runtime was Python 3.14.9 rather than CI’s 3.12. Helm, Prometheus-rule and frontend jobs were outside this Python lane.
