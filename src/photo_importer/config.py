"""Configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class WebConfig(BaseModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080


class HooksConfig(BaseModel):
    post_import: str | None = "/hooks/post_import.sh"
    on_error: str | None = "/hooks/on_error.sh"


class NotificationsConfig(BaseModel):
    webhook_url: str | None = None


class AppConfig(BaseModel):
    camera_name: str = "Fujifilm"
    destination: Path = Path("/photos")
    staging_dir: Path = Path("/tmp/xt3-import")
    database_path: Path = Path("/db/imports.sqlite3")
    log_dir: Path = Path("/logs")
    wait_seconds: float = 5.0
    unplug_settle_seconds: float = 3.0
    max_retries: int = 3
    retry_delay_seconds: float = 10.0
    delete_after_import: bool = False
    verify_hashes: bool = True
    raw_extensions: list[str] = Field(
        default_factory=lambda: ["RAF", "RAW", "DNG", "CR2", "CR3", "NEF", "ARW"]
    )
    jpeg_extensions: list[str] = Field(default_factory=lambda: ["JPG", "JPEG"])
    folder_layout: str = "ymd"
    preserve_filenames: bool = True
    dry_run: bool = False
    web: WebConfig = Field(default_factory=WebConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    @field_validator("raw_extensions", "jpeg_extensions", mode="before")
    @classmethod
    def upper_exts(cls, v: Any) -> list[str]:
        if not v:
            return []
        return [str(x).upper().lstrip(".") for x in v]

    @field_validator("folder_layout")
    @classmethod
    def valid_layout(cls, v: str) -> str:
        allowed = {"ymd", "ym_ymd"}
        if v not in allowed:
            raise ValueError(f"folder_layout must be one of {allowed}")
        return v

    @property
    def all_media_extensions(self) -> set[str]:
        return set(self.raw_extensions) | set(self.jpeg_extensions) | {"MOV", "MP4"}

    def ensure_dirs(self) -> None:
        self.destination.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def default_config_path() -> Path:
    env = os.environ.get("PHOTO_IMPORTER_CONFIG")
    if env:
        return Path(env)
    # Prefer mounted config, then example next to CWD
    candidates = [
        Path("/config/config.yaml"),
        Path("config/config.yaml"),
        Path("config.example.yaml"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return Path("config.example.yaml")


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or default_config_path()
    if config_path.is_file():
        with config_path.open() as fh:
            raw = yaml.safe_load(fh) or {}
    else:
        raw = {}
    return AppConfig.model_validate(raw)
