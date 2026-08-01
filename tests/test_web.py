"""State store and FastAPI health smoke tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from photo_importer.config import AppConfig
from photo_importer.database import Database
from photo_importer.state import State, status_store
from photo_importer.web import create_app


def test_status_store_force_import():
    status_store.request_force_import()
    assert status_store.consume_force_import() is True
    assert status_store.consume_force_import() is False


def test_api_health(tmp_path: Path):
    cfg = AppConfig(
        destination=tmp_path / "photos",
        staging_dir=tmp_path / "staging",
        database_path=tmp_path / "imports.sqlite3",
        log_dir=tmp_path / "logs",
    )
    cfg.ensure_dirs()
    database = Database(cfg.database_path)
    status_store.set_state(State.WAITING, "Waiting for camera")
    client = TestClient(create_app(cfg, database))
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert client.get("/").status_code == 200
    assert client.post("/api/import").json()["ok"] is True
