"""Application entrypoint — starts watcher + optional web UI."""

from __future__ import annotations

import argparse
import shutil
import threading
from pathlib import Path

import uvicorn

from photo_importer.config import AppConfig, default_config_path, load_config
from photo_importer.database import Database
from photo_importer.logger import get_logger, setup_logging
from photo_importer.watcher import Watcher, install_signal_handlers
from photo_importer.web import create_app


def _bootstrap_config(config_path: Path) -> None:
    """Copy example config into place if the mounted config is missing."""
    if config_path.is_file():
        return
    example = Path("/app/config.example.yaml")
    if not example.is_file():
        example = Path("config.example.yaml")
    if example.is_file():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(example, config_path)


def build_services(config: AppConfig) -> tuple[Database, Watcher]:
    config.ensure_dirs()
    database = Database(config.database_path)
    return database, Watcher(config, database)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Photo Importer service")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Disable the web dashboard",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll cycle in the foreground (for testing)",
    )
    args = parser.parse_args(argv)

    config_path = args.config or default_config_path()
    _bootstrap_config(config_path)
    config = load_config(config_path)

    setup_logging(config.log_dir)
    log = get_logger("main")
    log.info("Loaded config from %s", config_path)
    log.info(
        "Destination=%s camera_filter=%r dry_run=%s",
        config.destination,
        config.camera_name,
        config.dry_run,
    )

    database, watcher = build_services(config)
    install_signal_handlers(watcher)

    if args.once:
        watcher.start(background=False)
        return

    watcher.start(background=True)

    if args.no_web or not config.web.enabled:
        log.info("Web UI disabled — watcher running in foreground")
        try:
            while True:
                threading.Event().wait(3600)
        except KeyboardInterrupt:
            watcher.stop()
        return

    app = create_app(config, database)
    log.info("Starting web UI on %s:%s", config.web.host, config.web.port)
    try:
        uvicorn.run(
            app,
            host=config.web.host,
            port=config.web.port,
            log_level="info",
            access_log=False,
        )
    finally:
        watcher.stop()


if __name__ == "__main__":
    main()
