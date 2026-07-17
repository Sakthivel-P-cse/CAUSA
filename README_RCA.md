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

Owned packages: ``contracts/``, ``graphs/``, ``scoring/``, ``isolation/``.
Fixtures live in ``fixtures/`` (S0 DB-origin, S1 infra-origin).
"""
