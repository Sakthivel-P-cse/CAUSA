"""
ingest.trigger — the active RCA-trigger service (Dev_A brief §3, PRD2 §4).

Receives Alertmanager webhooks, opens an incident analysis window (via the C1
reader), and persists incident state to Postgres. It runs IN FRONT of the
Stage-2 RCA engine (chain, don't replace): open the window + record state here,
then forward to Stage 2. That keeps window-opening + incident state — squarely
Dev A's responsibility — out of Dev B's engine.

  webhook.py — parse the Alertmanager payload → Alert / window (pure, testable)
  state.py   — Postgres incidents + ground-truth writer (psycopg, lazy import)
  app.py     — /trigger + /health HTTP server
"""
