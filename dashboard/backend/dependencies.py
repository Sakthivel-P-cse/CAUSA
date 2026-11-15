from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request

from dashboard.backend.db import DashboardStore

logger = logging.getLogger(__name__)

_INCIDENT_DETECTION = Path(__file__).resolve().parents[2] / "incident-detection"
if str(_INCIDENT_DETECTION) not in sys.path:
    sys.path.insert(0, str(_INCIDENT_DETECTION))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def window_bounds(
    minutes: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[datetime, datetime]:
    if start is not None and end is not None:
        return start, end
    end_dt = end or _utcnow()
    start_dt = start or (end_dt - timedelta(minutes=minutes or 60))
    return start_dt, end_dt


def init_resources(app: FastAPI) -> None:
    from ingest.clients.loki_client import LokiClient
    from ingest.clients.prometheus_client import PrometheusClient
    from ingest.clients.tempo_client import TempoClient

    app.state.prometheus = PrometheusClient(
        os.getenv("PROMETHEUS_URL", "http://localhost:9090")
    )
    app.state.tempo = TempoClient(
        os.getenv("TEMPO_URL", "http://localhost:3200")
    )
    app.state.loki = LokiClient(
        os.getenv("LOKI_URL", "http://localhost:3100")
    )
    app.state.store = DashboardStore()


def close_resources(app: FastAPI) -> None:
    for name in ("prometheus", "tempo", "loki", "store"):
        client = getattr(app.state, name, None)
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.exception("Failed to close %s", name)
            finally:
                delattr(app.state, name)


@asynccontextmanager
async def dashboard_lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        init_resources(app)
        yield
    finally:
        close_resources(app)


def _resource(request: Request, name: str):
    resource = getattr(request.app.state, name, None)
    if resource is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "backend_not_initialized", "message": "Dashboard backend is unavailable"},
        )
    return resource


def get_prometheus(request: Request):
    return _resource(request, "prometheus")


def get_loki(request: Request):
    return _resource(request, "loki")


def get_tempo(request: Request):
    return _resource(request, "tempo")


def get_store(request: Request) -> DashboardStore:
    return _resource(request, "store")
