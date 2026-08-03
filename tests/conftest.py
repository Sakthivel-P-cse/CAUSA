"""Shared test fixtures for the Dev-A lane."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "eval" / "fixtures" / "golden_incident"


def ts(base_unix: float, minute: int) -> float:
    return base_unix + minute * 60


@pytest.fixture
def golden_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def tempo_trace_payload() -> dict:
    """The recorded golden OTLP trace payload (Tempo /api/traces/{id} shape)."""
    return json.loads((FIXTURES_DIR / "recorded" / "tempo_trace.json").read_text())


@pytest.fixture
def base_unix() -> float:
    return datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc).timestamp()


class FakeAdapter:
    """
    Minimal stand-in for TelemetryAdapter.metrics() — returns canned
    [[ts, value]] series per (node, metric). Lets anomaly tests run with zero
    HTTP and zero cluster.
    """

    def __init__(self, data: dict[tuple[str, str], list[list[float]]]):
        self.data = data

    def metrics(self, node: str, metric: str, window, namespace=None) -> list[list[float]]:
        return self.data.get((node, metric), [])


@pytest.fixture
def fake_adapter_cls():
    return FakeAdapter
