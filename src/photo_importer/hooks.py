"""Post-import and error hook runners + optional webhook notifications."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from photo_importer.config import AppConfig
from photo_importer.logger import get_logger

log = get_logger("hooks")


def run_hook(
    script_path: str | Path | None,
    *,
    env: dict[str, str] | None = None,
    timeout: float = 600,
) -> bool:
    """Execute a hook script if it exists and is executable / a shell script."""
    if not script_path:
        return True
    path = Path(script_path)
    if not path.is_file():
        log.debug("Hook not present: %s", path)
        return True

    merged_env = {**os.environ, **(env or {})}
    log.info("Running hook: %s", path)
    try:
        result = subprocess.run(
            ["bash", str(path)],
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.error("Hook timed out: %s", path)
        return False
    except Exception as exc:  # noqa: BLE001
        log.error("Hook failed to start (%s): %s", path, exc)
        return False

    if result.stdout:
        log.info("Hook stdout:\n%s", result.stdout.strip())
    if result.stderr:
        log.warning("Hook stderr:\n%s", result.stderr.strip())
    if result.returncode != 0:
        log.error("Hook exited %s: %s", result.returncode, path)
        return False
    return True


def _write_manifest(session_id: str, paths: Sequence[Path]) -> Path | None:
    """Write newline-delimited absolute paths for the hook to consume."""
    if not paths:
        return None
    safe_id = "".join(char for char in session_id if char.isalnum() or char in "-_")
    manifest = Path(tempfile.gettempdir()) / f"photo-importer-{safe_id or 'session'}.manifest"
    try:
        manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not write import manifest: %s", exc)
        return None
    return manifest


def run_post_import(
    config: AppConfig,
    *,
    session_id: str,
    destination: Path,
    files_imported: int,
    files_skipped: int,
    camera_name: str | None,
    imported_paths: Sequence[Path] | None = None,
) -> bool:
    env = {
        "IMPORT_SESSION_ID": session_id,
        "IMPORT_DESTINATION": str(destination),
        "IMPORT_FILES_IMPORTED": str(files_imported),
        "IMPORT_FILES_SKIPPED": str(files_skipped),
        "IMPORT_CAMERA_NAME": camera_name or "",
    }

    # Hand the hook the exact files from this session via a manifest file rather
    # than argv, so a full card's worth of paths cannot overflow the arg list.
    manifest = _write_manifest(session_id, imported_paths or [])
    if manifest is not None:
        env["IMPORT_MANIFEST"] = str(manifest)

    try:
        # Uploads can take a long time on a full card; do not cut them off.
        ok = run_hook(config.hooks.post_import, env=env, timeout=6 * 60 * 60)
    finally:
        if manifest is not None:
            manifest.unlink(missing_ok=True)

    _notify_webhook(
        config,
        {
            "event": "import_complete",
            "session_id": session_id,
            "destination": str(destination),
            "files_imported": files_imported,
            "files_skipped": files_skipped,
            "camera_name": camera_name,
        },
    )
    return ok


def run_on_error(
    config: AppConfig,
    *,
    session_id: str | None,
    error: str,
    camera_name: str | None,
) -> bool:
    env = {
        "IMPORT_SESSION_ID": session_id or "",
        "IMPORT_ERROR": error,
        "IMPORT_CAMERA_NAME": camera_name or "",
    }
    ok = run_hook(config.hooks.on_error, env=env)
    _notify_webhook(
        config,
        {
            "event": "import_error",
            "session_id": session_id,
            "error": error,
            "camera_name": camera_name,
        },
    )
    return ok


def _notify_webhook(config: AppConfig, payload: dict[str, Any]) -> None:
    url = config.notifications.webhook_url
    if not url:
        return
    # Discord-friendly content wrapper if the URL looks like Discord
    body: dict[str, Any]
    if "discord.com" in url or "discordapp.com" in url:
        body = {
            "content": _format_discord(payload),
        }
    else:
        body = payload

    try:
        response = httpx.post(url, json=body, timeout=15.0)
        response.raise_for_status()
        log.info("Webhook notified (%s)", payload.get("event"))
    except Exception as exc:  # noqa: BLE001
        log.warning("Webhook failed: %s", exc)


def _format_discord(payload: dict[str, Any]) -> str:
    event = payload.get("event")
    if event == "import_complete":
        return (
            f"**XT3 Import complete**\n"
            f"Camera: {payload.get('camera_name')}\n"
            f"Imported: {payload.get('files_imported')} "
            f"(skipped {payload.get('files_skipped')})\n"
            f"Session: `{payload.get('session_id')}`"
        )
    return f"**XT3 Import error**\n```{json.dumps(payload, indent=2)[:1800]}```"
