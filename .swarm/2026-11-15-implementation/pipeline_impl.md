# Pipeline implementation

Exact paired persisted incident bounds now pass through the reader and scope both error-first and fallback automatic Tempo search. Recent-search defaults and preferred trace bypass remain. The reader rejects missing, partial and nonpositive bounds; the orchestrator retains cleanup.

Red: historical regressions failed with unexpected window_start/start keywords (9 failures). Green: `PYTHONPATH=incident-detection:. /tmp/causa-test-venv/bin/python -m pytest -q incident-detection/tests/test_historical_window.py incident-detection/tests/rca/test_orchestrator.py incident-detection/tests/test_trigger_service.py incident-detection/tests/test_client_hardening.py incident-detection/tests/test_adapters_http.py` — 29 passed. `git diff --check` passed for tracked assigned paths.

Touched reader.py, tempo_client.py, rca/orchestrator.py, tests/rca/test_orchestrator.py, new tests/test_historical_window.py. No live Tempo or cluster checks. Tempo serializes search timestamps to integer seconds; preferred IDs are not window constrained by design. Full suite not run by this agent.
