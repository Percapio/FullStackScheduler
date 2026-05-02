from __future__ import annotations

import os, sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .assemblies import router as assemblies_router
from .jobs import router as jobs_router
from .staging import router as staging_router
from .ingest import router as ingest_router

_DEV_ORIGINS: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")


def _dist_dir() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller extracts --add-data here at runtime.
        return Path(sys._MEIPASS) / "dist"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[3] / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="Scheduler API", version="0.4.0")

    if not getattr(sys, "frozen", False):
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(_DEV_ORIGINS),
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
            expose_headers=["X-Total-Count"],
        )

    app.include_router(staging_router, prefix="/api/staging", tags=["staging"])
    app.include_router(jobs_router, prefix="/api/jobs", tags=["jobs"])
    app.include_router(assemblies_router, prefix="/api/assemblies", tags=["assemblies"])
    app.include_router(ingest_router, prefix="/api/ingest", tags=["ingest"])

    dist = _dist_dir()
    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{catchall:path}", include_in_schema=False)
    def spa_fallback(catchall: str):
        if catchall.startswith(("api/", "docs", "redoc", "openapi.json")):
            raise HTTPException(status_code=404)
        if catchall:
            candidate = dist / catchall
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(dist / "index.html")

    return app
