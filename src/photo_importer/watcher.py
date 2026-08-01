"""Camera watcher state machine."""

from __future__ import annotations

import signal
import threading
import time

from photo_importer import camera as cam
from photo_importer.config import AppConfig
from photo_importer.database import Database
from photo_importer.importer import ImportSession
from photo_importer.logger import get_logger
from photo_importer.state import State, status_store

log = get_logger("watcher")


class Watcher:
    """
    State machine:

        WAITING
           ↓
        CAMERA_FOUND
           ↓
        IMPORTING → VERIFYING → ORGANIZING → POST_PROCESS
           ↓
        WAIT_FOR_UNPLUG
           ↓
        WAITING
    """

    def __init__(self, config: AppConfig, database: Database) -> None:
        self.config = config
        self.database = database
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, *, background: bool = True) -> None:
        if background:
            self._thread = threading.Thread(target=self._loop, name="camera-watcher", daemon=True)
            self._thread.start()
        else:
            self._loop()

    def stop(self) -> None:
        log.info("Stopping watcher…")
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=15)

    def _loop(self) -> None:
        log.info(
            "Watcher started — looking for cameras matching %r every %ss",
            self.config.camera_name,
            self.config.wait_seconds,
        )
        status_store.set_state(State.WAITING, "Waiting for camera")

        while not self._stop.is_set():
            try:
                force = status_store.consume_force_import()
                detected = cam.camera_present(self.config.camera_name)

                if detected or force:
                    if force and not detected:
                        log.warning("Force import requested but no camera detected")
                        status_store.set_state(State.WAITING, "No camera for force import")
                    elif detected:
                        self._handle_camera(detected)
                else:
                    status_store.set_state(State.WAITING, "Waiting for camera")
                    status_store.update(camera_name=None, camera_port=None)

            except Exception as exc:  # noqa: BLE001
                log.exception("Watcher loop error: %s", exc)
                status_store.set_state(State.ERROR, str(exc))
                status_store.update(last_error=str(exc))

            self._stop.wait(self.config.wait_seconds)

        log.info("Watcher stopped")

    def _handle_camera(self, detected: cam.DetectedCamera) -> None:
        log.info("Camera found: %s on %s", detected.model, detected.port)
        session = ImportSession(self.config, self.database)
        ok = session.run(detected)

        # Always wait for unplug so we don't re-import immediately
        status_store.set_state(
            State.WAIT_FOR_UNPLUG,
            "Import done — waiting for camera unplug" if ok else "Error — unplug camera to reset",
        )
        self._wait_until_unplugged()
        status_store.reset_progress()
        status_store.update(session_id=None, camera_name=None, camera_port=None)
        status_store.set_state(State.WAITING, "Waiting for camera")

    def _wait_until_unplugged(self) -> None:
        log.info("Waiting for camera to be unplugged…")
        while not self._stop.is_set():
            if status_store.consume_eject():
                log.info("Eject requested via UI — returning to WAITING")
                break
            if cam.camera_present(self.config.camera_name) is None:
                log.info("Camera disconnected")
                time.sleep(self.config.unplug_settle_seconds)
                break
            self._stop.wait(self.config.wait_seconds)


def install_signal_handlers(watcher: Watcher) -> None:
    def _handler(signum: int, _frame: object) -> None:
        log.info("Received signal %s", signum)
        watcher.stop()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
