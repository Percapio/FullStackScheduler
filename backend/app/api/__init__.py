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


def warm_lazy_singletons() -> None:
    from ..db import get_engine, get_session_factory
    get_engine()
    get_session_factory()


def freeze_import_time_state() -> None:
    import gc
    warm_lazy_singletons()
    gc.collect()
    gc.freeze()


from contextlib import asynccontextmanager
import queue
import asyncio

@asynccontextmanager
async def application_lifespan(app: FastAPI):
    from ..config import get_settings
    from ..updates import ScheduleUpdateHub, EventPublisher
    from ..services.photo_warm import shutdown_warm_worker
    import logging
    logger = logging.getLogger(__name__)

    settings = get_settings()
    hub = ScheduleUpdateHub(settings)
    q = queue.Queue(maxsize=settings.ws_publish_queue_max)
    publisher = EventPublisher(q)

    app.state.hub = hub
    app.state.publisher = publisher

    async def drain_task():
        while True:
            try:
                event = await asyncio.to_thread(q.get, True, 0.5)
                await hub.fan_out(event)
            except queue.Empty:
                pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"WebSocket drain task error: {e}")
                await asyncio.sleep(settings.ws_drain_restart_backoff_seconds)

    task = asyncio.create_task(drain_task())

    yield

    await hub.close_all()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    shutdown_warm_worker()


def create_app() -> FastAPI:
    app = FastAPI(title="Scheduler API", version="0.4.0", lifespan=application_lifespan)

    from starlette.middleware.gzip import GZipMiddleware
    # Static assets ship ~375 KB uncompressed; gzip takes that to ~109 KB.
    # Added BEFORE CORSMiddleware so CORS ends up outermost (add_middleware
    # prepends), keeping CORS headers on every response including errors.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

    if not getattr(sys, "frozen", False):
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(_DEV_ORIGINS),
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["*"],
            expose_headers=["X-Total-Count"],
        )

    app.include_router(staging_router, prefix="/api/staging", tags=["staging"])
    app.include_router(jobs_router, prefix="/api/jobs", tags=["jobs"])
    app.include_router(assemblies_router, prefix="/api/assemblies", tags=["assemblies"])
    app.include_router(ingest_router, prefix="/api/ingest", tags=["ingest"])
    
    from .photos import router as photos_router
    app.include_router(photos_router, prefix="/api/photos", tags=["photos"])
    from .settings import settings_router
    app.include_router(settings_router, prefix="/api/settings", tags=["settings"])

    @app.websocket("/api/ws/updates")
    async def updates_ws(websocket: __import__('starlette').websockets.WebSocket, client_id: str | None = None):
        await websocket.accept()
        from ..updates import WebSocketConnection
        hub = websocket.app.state.hub
        settings = hub._settings
        
        conn = WebSocketConnection(socket=websocket, client_id=client_id)
        outcome = hub.register(conn, client_id)
        
        if outcome == "RejectedAtCapacity":
            await websocket.close(code=1008, reason="Capacity exceeded")
            return

        async def heartbeat_loop():
            while not conn.closing:
                await asyncio.sleep(settings.ws_heartbeat_seconds)
                if conn.closing:
                    break
                try:
                    await websocket.send_json({
                        "type": "heartbeat",
                        "heartbeat_seconds": settings.ws_heartbeat_seconds
                    })
                except Exception:
                    break

        hb_task = asyncio.create_task(heartbeat_loop())
        try:
            while True:
                await websocket.receive()
        except Exception:
            pass
        finally:
            hb_task.cancel()
            hub.deregister(conn)

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

    from ..config import get_settings
    if get_settings().gc_freeze_after_startup:
        freeze_import_time_state()

    return app
