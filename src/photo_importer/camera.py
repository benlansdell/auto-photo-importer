"""Camera detection and gphoto2 interactions."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from photo_importer.logger import get_logger

log = get_logger("camera")


@dataclass(frozen=True)
class DetectedCamera:
    model: str
    port: str


class CameraError(Exception):
    """Raised when a gphoto2 operation fails."""


def _run(
    args: list[str],
    *,
    timeout: float | None = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    log.debug("Running: %s", " ".join(args))
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CameraError(
            "gphoto2 not found — install gphoto2 or run inside the Docker image"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CameraError(f"Command timed out: {' '.join(args)}") from exc

    if check and result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise CameraError(stderr or f"Command failed ({result.returncode}): {' '.join(args)}")
    return result


def gphoto2_available() -> bool:
    return shutil.which("gphoto2") is not None


def detect_cameras(name_filter: str | None = None) -> list[DetectedCamera]:
    """Parse `gphoto2 --auto-detect` for connected PTP cameras."""
    result = _run(["gphoto2", "--auto-detect"], timeout=30, check=False)
    output = result.stdout or ""
    cameras: list[DetectedCamera] = []

    # Output looks like:
    # Model                          Port
    # ----------------------------------------------------------
    # Fujifilm X-T3                  usb:001,004
    for line in output.splitlines():
        line = line.rstrip()
        if not line or line.startswith("Model") or set(line) <= {"-", " "}:
            continue
        match = re.match(r"^(.+?)\s+(usb:\S+|ptpip:\S+|disk:\S+)\s*$", line)
        if not match:
            continue
        model = match.group(1).strip()
        port = match.group(2).strip()
        if model.lower() in {"model", "unknown"}:
            continue
        if name_filter and name_filter.lower() not in model.lower():
            continue
        cameras.append(DetectedCamera(model=model, port=port))

    return cameras


def camera_present(name_filter: str | None = None) -> DetectedCamera | None:
    cameras = detect_cameras(name_filter)
    return cameras[0] if cameras else None


def get_summary(port: str | None = None) -> str:
    args = ["gphoto2", "--summary"]
    if port:
        args.extend(["--port", port])
    result = _run(args, timeout=60, check=False)
    return result.stdout or result.stderr or ""


def extract_serial(summary: str) -> str | None:
    for pattern in (
        r"Serial Number:\s*(\S+)",
        r"SerialNo:\s*(\S+)",
        r"Camera Identification:\s*(\S+)",
    ):
        match = re.search(pattern, summary, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def list_files(port: str | None = None) -> list[str]:
    """Return basenames of files on the camera."""
    args = ["gphoto2", "--list-files"]
    if port:
        args.extend(["--port", port])
    result = _run(args, timeout=120, check=False)
    names: list[str] = []
    # Lines like: #1     DSCF0001.RAF               rd  25123456 image/x-fuji-raf
    for line in (result.stdout or "").splitlines():
        match = re.search(r"#\d+\s+(\S+\.\w+)", line)
        if match:
            names.append(match.group(1))
    return names


def download_all(
    dest_dir: Path,
    *,
    port: str | None = None,
    skip_existing: bool = True,
) -> None:
    """Download all files from the camera into dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename_pattern = str(dest_dir / "%f.%C")
    args = [
        "gphoto2",
        "--get-all-files",
        "--filename",
        filename_pattern,
    ]
    if skip_existing:
        args.append("--skip-existing")
    if port:
        args.extend(["--port", port])

    # Downloads can take a long time for large sessions
    result = _run(args, timeout=None, check=False)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        # gphoto2 sometimes returns non-zero even on partial success; surface the message
        log.warning("gphoto2 download exit %s: %s", result.returncode, stderr)
        # Treat as failure only if nothing landed
        files = list(dest_dir.rglob("*"))
        if not any(p.is_file() for p in files):
            raise CameraError(stderr or "Download failed with no files")


def delete_all_files(port: str | None = None) -> None:
    """Delete all files from the camera. Use only after verified import."""
    args = ["gphoto2", "--delete-all-files", "--recurse"]
    if port:
        args.extend(["--port", port])
    # Confirmation is non-interactive when stdin is not a TTY, but some builds
    # still want --force / yes piping. We pipe "y\n" just in case.
    log.warning("Deleting all files from camera (port=%s)", port)
    try:
        proc = subprocess.run(
            args,
            input="y\n",
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise CameraError(f"Failed to delete camera files: {exc}") from exc
    if proc.returncode != 0:
        raise CameraError((proc.stderr or proc.stdout or "delete failed").strip())
