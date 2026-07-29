from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard.backend.dependencies import dashboard_lifespan
from dashboard.backend.routers import alerts, graphs, incidents, logs, metrics, remediation, stats, traces

app = FastAPI(title="Causa Dashboard", lifespan=dashboard_lifespan)

_cors_origins = [
    origin.strip()
    for origin in os.getenv("CAUSA_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=bool(_cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def authenticate_api(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path != "/api/health":
        from dashboard.backend.auth import AuthenticationError, authenticate

        try:
            request.state.identity = authenticate(request.headers.get("Authorization"))
        except AuthenticationError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )
    return await call_next(request)

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
    return {"status": "alive", "service": "causa-dashboard"}


@app.get("/api/health")
def api_health(request: Request):
    checks = {}
    store = getattr(request.app.state, "store", None)
    checks["initialized"] = store is not None
    if store is not None and store._use_pg:
        try:
            with store._connect() as conn:
                conn.execute("SELECT 1")
            checks["postgres"] = True
        except Exception:
            checks["postgres"] = False
    else:
        checks["postgres"] = False
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "service": "causa-dashboard",
            "checks": checks,
        },
    )


@app.get("/api/session")
def api_session(request: Request):
    identity = request.state.identity
    return {
        "authenticated": identity.subject != "development-auth-disabled",
        "subject": identity.subject,
        "role": identity.role,
    }


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
