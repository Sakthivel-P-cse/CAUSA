"""Causa RCA Engine (Dev B) — graphs, deterministic scoring, isolation.

Quick start::

    python -m venv .venv && .venv/bin/pip install pytest
    .venv/bin/python -m pytest tests/ -v

Pipeline::

    topology + traces + AnomalyOutput
      → four graphs + Incident Subgraph
      → Influence Graph (IG)
      → T/D/P → layer arbitration
      → RootCauseCandidate list (templated explanations)

Docs:
  - ``docs/rca_kernel.md`` — M6 kernel writeup + explanation examples
  - ``docs/scorer_c4.md`` — C4 Scorer seam for Dev C

Owned packages: ``contracts/``, ``graphs/``, ``scoring/``, ``isolation/``.
Fixtures: golden, S0, S1, S5, D1–D3 under ``fixtures/``.
"""
