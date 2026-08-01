"""Single import session orchestration."""

from __future__ import annotations

import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from photo_importer import camera as cam
from photo_importer.config import AppConfig
from photo_importer.database import Database, ImportRecord
from photo_importer.hooks import run_on_error, run_post_import
from photo_importer.logger import get_logger
from photo_importer.organizer import organize_files
from photo_importer.state import State, status_store
from photo_importer.verifier import hash_directory, sha256_file

log = get_logger("importer")


class ImportSession:
    def __init__(self, config: AppConfig, database: Database) -> None:
        self.config = config
        self.database = database

    def run(self, detected: cam.DetectedCamera) -> bool:
        """
        Execute a full import session.

        Returns True on success (including 'nothing new to import').
        """
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        session_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
        status_store.update(
            session_id=session_id,
            camera_name=detected.model,
            camera_port=detected.port,
        )
        status_store.reset_progress()
        status_store.set_state(State.CAMERA_FOUND, f"Detected {detected.model}")
        log.info("Camera connected: %s (port=%s)", detected.model, detected.port)

        # Reading the summary is deferred until after the download: closing a PTP
        # session makes Fujifilm bodies re-enumerate at a new USB address.
        serial: str | None = None
        self.database.start_session(session_id, detected.model, serial)

        staging = self.config.staging_dir / session_id
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        imported = 0
        skipped = 0

        try:
            if self.config.dry_run:
                names = cam.list_files(self._current_port(detected))
                log.info(
                    "[dry-run] Camera has %d files: %s",
                    len(names),
                    ", ".join(names[:20]),
                )
                status_store.update(
                    progress={"files_total": len(names), "message": "Dry run"}
                )
                self.database.finish_session(
                    session_id,
                    files_imported=0,
                    files_skipped=0,
                    status="dry_run",
                )
                return True

            # --- Download ---
            status_store.set_state(State.IMPORTING, "Downloading from camera")
            files_on_camera = []
            try:
                files_on_camera = cam.list_files(self._current_port(detected))
            except cam.CameraError as exc:
                log.warning("Could not list files before download: %s", exc)

            status_store.update(
                progress={
                    "files_total": len(files_on_camera),
                    "message": f"Downloading {len(files_on_camera) or '?'} files",
                }
            )
            log.info("Downloading files to %s", staging)
            self._download_with_retries(detected, staging)

            serial = self._read_serial(detected)

            downloaded = [p for p in staging.rglob("*") if p.is_file()]
            status_store.update(
                progress={
                    "files_downloaded": len(downloaded),
                    "files_total": len(downloaded) or len(files_on_camera),
                    "message": f"Downloaded {len(downloaded)} files",
                }
            )
            log.info("Downloaded %d files", len(downloaded))

            if not downloaded:
                log.info("No files downloaded (camera empty or all skipped by gphoto2)")
                self.database.finish_session(
                    session_id, files_imported=0, files_skipped=0
                )
                status_store.update(last_import_at=datetime.now(UTC))
                return True

            # Hashes are the durable duplicate identity stored in SQLite.
            status_store.set_state(State.VERIFYING, "Computing SHA-256 hashes")
            hashes = hash_directory(staging)
            status_store.update(
                progress={
                    "files_verified": len(hashes),
                    "message": f"Checked {len(hashes)} content hashes",
                }
            )

            new_files: list[Path] = []
            for path, digest in hashes.items():
                if self.database.has_hash(digest):
                    log.info("Skipping duplicate %s (%s…)", path.name, digest[:12])
                    skipped += 1
                    path.unlink(missing_ok=True)
                else:
                    new_files.append(path)

            status_store.update(progress={"files_skipped": skipped})

            if not new_files:
                log.info("All downloaded files already exist in the import database")
                shutil.rmtree(staging, ignore_errors=True)
                self.database.finish_session(
                    session_id,
                    files_imported=0,
                    files_skipped=skipped,
                )
                status_store.update(last_import_at=datetime.now(UTC))
                return True

            remaining_hashes = {path: hashes[path] for path in new_files}

            # --- Organize ---
            status_store.set_state(State.ORGANIZING, "Organizing into date folders")
            organized = organize_files(staging, self.config, hashes=remaining_hashes)

            for item in organized:
                digest = item.sha256 or sha256_file(item.destination)
                if self.config.verify_hashes:
                    dest_hash = sha256_file(item.destination)
                    if dest_hash != digest:
                        raise RuntimeError(
                            f"Post-move hash mismatch for {item.destination.name}"
                        )
                    digest = dest_hash

                record = ImportRecord(
                    camera_serial=serial,
                    camera_name=detected.model,
                    filename=item.destination.name,
                    capture_time=(
                        item.capture_time.isoformat() if item.capture_time else None
                    ),
                    sha256=digest,
                    destination=str(item.destination),
                    file_size=item.destination.stat().st_size,
                    session_id=session_id,
                    import_time=datetime.now(UTC).isoformat(),
                )
                if self.database.record_import(record):
                    imported += 1
                else:
                    skipped += 1

            status_store.update(
                progress={
                    "files_imported": imported,
                    "files_skipped": skipped,
                    "message": f"Imported {imported} files",
                }
            )

            # Cleanup empty staging
            shutil.rmtree(staging, ignore_errors=True)

            # --- Optional camera wipe ---
            if self.config.delete_after_import and imported > 0:
                log.warning("delete_after_import enabled — wiping camera files")
                try:
                    cam.delete_all_files(self._current_port(detected))
                except cam.CameraError as exc:
                    log.error("Camera wipe failed (library is safe): %s", exc)

            # --- Post process ---
            status_store.set_state(State.POST_PROCESS, "Running post-import hooks")
            run_post_import(
                self.config,
                session_id=session_id,
                destination=self.config.destination,
                files_imported=imported,
                files_skipped=skipped,
                camera_name=detected.model,
            )

            self.database.finish_session(
                session_id,
                files_imported=imported,
                files_skipped=skipped,
            )
            status_store.update(last_import_at=datetime.now(UTC), last_error=None)
            log.info(
                "Import session %s complete: imported=%d skipped=%d",
                session_id,
                imported,
                skipped,
            )
            return True

        except Exception as exc:  # noqa: BLE001
            log.exception("Import session failed: %s", exc)
            status_store.update(last_error=str(exc))
            status_store.set_state(State.ERROR, str(exc))
            self.database.finish_session(
                session_id,
                files_imported=imported,
                files_skipped=skipped,
                status="error",
                error_message=str(exc),
            )
            run_on_error(
                self.config,
                session_id=session_id,
                error=str(exc),
                camera_name=detected.model,
            )
            return False
        finally:
            # Leave staging on error for inspection; clean if empty
            if staging.exists() and not any(staging.rglob("*")):
                shutil.rmtree(staging, ignore_errors=True)

    def _current_port(self, detected: cam.DetectedCamera) -> str | None:
        """
        Re-detect the camera to get its current USB address.

        Fujifilm bodies re-enumerate after each PTP session closes, so a port
        captured earlier in the session often fails with libgphoto2 error -52.
        Returning None lets gphoto2 pick the single attached camera itself.
        """
        try:
            refreshed = cam.camera_present(self.config.camera_name)
        except cam.CameraError as exc:
            log.warning("Could not re-detect camera: %s", exc)
            return None

        if refreshed is None:
            log.warning("Camera no longer listed by gphoto2 — letting it auto-detect")
            return None

        if refreshed.port != detected.port:
            log.info(
                "Camera re-enumerated: %s is now on %s",
                detected.port,
                refreshed.port,
            )
        return refreshed.port

    def _read_serial(self, detected: cam.DetectedCamera) -> str | None:
        try:
            summary = cam.get_summary(self._current_port(detected))
        except cam.CameraError as exc:
            log.warning("Could not read camera summary: %s", exc)
            return None
        serial = cam.extract_serial(summary)
        log.info("Camera serial: %s", serial or "unknown")
        return serial

    def _download_with_retries(self, detected: cam.DetectedCamera, staging: Path) -> None:
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            if self.config.camera_settle_seconds > 0:
                time.sleep(self.config.camera_settle_seconds)
            try:
                cam.download_all(
                    staging,
                    port=self._current_port(detected),
                    skip_existing=True,
                )
                return
            except cam.CameraError as exc:
                last_error = exc
                log.warning(
                    "Download attempt %d/%d failed: %s",
                    attempt,
                    self.config.max_retries,
                    exc,
                )
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_delay_seconds)
        assert last_error is not None
        raise last_error
