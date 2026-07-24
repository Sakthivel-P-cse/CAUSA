from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard.backend.dependencies import dashboard_lifespan
from dashboard.backend.routers import alerts, graphs, incidents, logs, metrics, remediation, stats, traces

app = FastAPI(title="Causa Dashboard", lifespan=dashboard_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CAUSA_CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(metrics.router, prefix="/api/metrics", tags=["metrics"])
app.include_router(logs.router, prefix="/api/logs", tags=["logs"])
app.include_router(traces.router, prefix="/api/traces", tags=["traces"])
app.include_router(incidents.router, prefix="/api/incidents", tags=["incidents"])
app.include_router(stats.router, prefix="/api/stats", tags=["stats"])
app.include_router(graphs.router, prefix="/api/graphs", tags=["graphs"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(remediation.router, prefix="/api/remediation", tags=["remediation"])


@app.get("/health")
def health():
    return {"status": "ok", "service": "causa-dashboard"}


@app.get("/api/health")
def api_health():
    return {"status": "ok", "service": "causa-dashboard"}


_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str = ""):
        index = _DIST / "index.html"
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
