"""Import state machine states and shared status."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class State(StrEnum):
    WAITING = "waiting"
    CAMERA_FOUND = "camera_found"
    IMPORTING = "importing"
    VERIFYING = "verifying"
    ORGANIZING = "organizing"
    POST_PROCESS = "post_process"
    WAIT_FOR_UNPLUG = "wait_for_unplug"
    ERROR = "error"


@dataclass
class ImportProgress:
    files_total: int = 0
    files_downloaded: int = 0
    files_verified: int = 0
    files_imported: int = 0
    files_skipped: int = 0
    current_file: str | None = None
    message: str = ""


@dataclass
class AppStatus:
    state: State = State.WAITING
    camera_name: str | None = None
    camera_port: str | None = None
    last_import_at: datetime | None = None
    last_error: str | None = None
    progress: ImportProgress = field(default_factory=ImportProgress)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    session_id: str | None = None
    # Manual triggers from the web UI
    force_import: bool = False
    request_eject: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "camera_name": self.camera_name,
            "camera_port": self.camera_port,
            "last_import_at": self.last_import_at.isoformat() if self.last_import_at else None,
            "last_error": self.last_error,
            "progress": {
                "files_total": self.progress.files_total,
                "files_downloaded": self.progress.files_downloaded,
                "files_verified": self.progress.files_verified,
                "files_imported": self.progress.files_imported,
                "files_skipped": self.progress.files_skipped,
                "current_file": self.progress.current_file,
                "message": self.progress.message,
            },
            "started_at": self.started_at.isoformat(),
            "session_id": self.session_id,
        }


class StatusStore:
    """Thread-safe status shared between watcher and web UI."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._status = AppStatus()

    def get(self) -> AppStatus:
        with self._lock:
            return self._status

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._status.to_dict()

    def set_state(self, state: State, message: str = "") -> None:
        with self._lock:
            self._status.state = state
            if message:
                self._status.progress.message = message

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if key == "progress" and isinstance(value, dict):
                    for pk, pv in value.items():
                        setattr(self._status.progress, pk, pv)
                elif hasattr(self._status, key):
                    setattr(self._status, key, value)

    def reset_progress(self) -> None:
        with self._lock:
            self._status.progress = ImportProgress()

    def request_force_import(self) -> None:
        with self._lock:
            self._status.force_import = True

    def consume_force_import(self) -> bool:
        with self._lock:
            flag = self._status.force_import
            self._status.force_import = False
            return flag

    def request_eject(self) -> None:
        with self._lock:
            self._status.request_eject = True

    def consume_eject(self) -> bool:
        with self._lock:
            flag = self._status.request_eject
            self._status.request_eject = False
            return flag


# Global singleton used by web + watcher
status_store = StatusStore()
