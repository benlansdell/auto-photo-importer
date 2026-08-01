"""FastAPI operational dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from photo_importer import __version__
from photo_importer.config import AppConfig
from photo_importer.database import Database
from photo_importer.logger import get_recent_logs
from photo_importer.state import status_store

WEB_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def create_app(config: AppConfig, database: Database) -> FastAPI:
    app = FastAPI(title="Photo Importer", version=__version__)

    static_dir = WEB_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "version": __version__,
                "camera_filter": config.camera_name,
                "destination": str(config.destination),
                "delete_after": config.delete_after_import,
                "verify_hashes": config.verify_hashes,
                "dry_run": config.dry_run,
            },
        )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        snap = status_store.snapshot()
        return {
            "status": "ok",
            "version": __version__,
            "state": snap["state"],
        }

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        snap = status_store.snapshot()
        snap["stats"] = database.stats()
        snap["config"] = {
            "camera_name": config.camera_name,
            "destination": str(config.destination),
            "delete_after_import": config.delete_after_import,
            "verify_hashes": config.verify_hashes,
            "dry_run": config.dry_run,
            "folder_layout": config.folder_layout,
        }
        return snap

    @app.get("/api/logs")
    async def api_logs(limit: int = 100) -> dict[str, Any]:
        return {"logs": get_recent_logs(limit)}

    @app.get("/api/history")
    async def api_history(limit: int = 50) -> dict[str, Any]:
        return {
            "imports": database.recent_imports(limit),
            "sessions": database.recent_sessions(min(limit, 20)),
        }

    @app.post("/api/import")
    async def api_force_import() -> JSONResponse:
        status_store.request_force_import()
        return JSONResponse({"ok": True, "message": "Force import requested"})

    @app.post("/api/eject")
    async def api_eject() -> JSONResponse:
        status_store.request_eject()
        return JSONResponse({"ok": True, "message": "Eject / reset requested"})

    return app
