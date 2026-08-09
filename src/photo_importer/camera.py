"""Camera detection and gphoto2 interactions."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from photo_importer.logger import get_logger

log = get_logger("camera")


@dataclass(frozen=True)
class DetectedCamera:
    model: str
    port: str


@dataclass(frozen=True)
class CameraFile:
    """A file listed on the camera, with enough info for selective download."""

    index: int
    name: str
    folder: str
    # gphoto2 reports sizes in KB, not bytes. Kept in the unit it was printed in.
    size_kb: int | None = None


class CameraError(Exception):
    """Raised when a gphoto2 operation fails."""


_ERROR_NOISE = ("*** Error ***", "No error description available")

_SAVING_RE = re.compile(r"^Saving file as (.+)$")
_DOWNLOADING_RE = re.compile(r"^Downloading '([^']+)'")


def clean_message(text: str) -> str:
    """Condense gphoto2's multi-line error banners into a single log line."""
    parts: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().strip("*").strip()
        for noise in _ERROR_NOISE:
            line = line.replace(noise, "")
        line = line.strip(" :")
        if line:
            parts.append(line)
    return "; ".join(dict.fromkeys(parts)) or text.strip()


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
        stderr = clean_message(result.stderr or "")
        raise CameraError(stderr or f"Command failed ({result.returncode}): {' '.join(args)}")
    return result


def _stream(
    args: list[str],
    *,
    on_file: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """
    Run gphoto2 and consume its output as it arrives.

    Long transfers would otherwise produce no sign of life until they finish.
    Returns the exit code and the combined output for error reporting.
    """
    log.debug("Streaming: %s", " ".join(args))
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise CameraError(
            "gphoto2 not found — install gphoto2 or run inside the Docker image"
        ) from exc

    collected: list[str] = []
    reported: str | None = None
    assert process.stdout is not None
    with process.stdout:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            collected.append(line)
            if on_file is None:
                continue
            filename = _parse_filename(line)
            # gphoto2 announces each file twice ("Downloading 'x'" then
            # "Saving file as .../x"), so collapse consecutive repeats.
            if filename is not None and filename != reported:
                reported = filename
                on_file(filename)

    return process.wait(), "\n".join(collected)


def _parse_filename(line: str) -> str | None:
    saving = _SAVING_RE.match(line)
    if saving:
        return Path(saving.group(1)).name
    downloading = _DOWNLOADING_RE.match(line)
    return downloading.group(1) if downloading else None


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


USB_NODE_HINT = (
    "The USB device node is not visible here even though gphoto2 lists the camera "
    "(libusb enumerates via /sys, but opening the camera needs the node). In Docker, "
    "mount /dev/bus/usb as a volume rather than a `devices:` entry — `devices:` is "
    "resolved at container creation, so cameras plugged in later never appear."
)


def usb_node_path(port: str) -> Path | None:
    """Map a gphoto2 port such as ``usb:003,004`` to its /dev/bus/usb node."""
    match = re.fullmatch(r"usb:(\d+),(\d+)", port.strip())
    if not match:
        return None
    bus, device = match.groups()
    return Path("/dev/bus/usb") / bus / device


def missing_usb_node(port: str | None) -> Path | None:
    """Return the expected device node when it is absent but /dev/bus/usb exists."""
    if not port:
        return None
    node = usb_node_path(port)
    if node is None or not node.parent.parent.is_dir():
        return None
    return None if node.exists() else node


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


def list_files(port: str | None = None) -> list[CameraFile]:
    """Return files on the camera with folder + index for selective download."""
    args = ["gphoto2", "--list-files"]
    if port:
        args.extend(["--port", port])
    result = _run(args, timeout=120, check=False)
    if result.returncode != 0:
        raise CameraError(
            clean_message(result.stderr or "") or f"--list-files exited {result.returncode}"
        )
    return parse_file_list(result.stdout or "")


def parse_file_list(output: str) -> list[CameraFile]:
    """
    Parse ``gphoto2 --list-files`` output into folder-aware file entries.

    Lines look like::

        There are 2 files in folder '/store_00010001/DCIM/100_FUJI'.
        #1     DSCF0001.RAF               rd 25123 KB image/x-fuji-raf
        #2     DSCF0001.JPG               rd  5123 KB 6240x4160 image/jpeg 1700948606

    Optional columns (resolution, trailing timestamp) vary by camera, so the size
    is only taken from the field explicitly labelled ``KB``. Anything else leaves
    the size unknown, which makes the caller download the file and fall back to
    hashing rather than guessing.
    """
    files: list[CameraFile] = []
    folder = ""
    folder_re = re.compile(r"files? in folder '([^']+)'", re.IGNORECASE)
    file_re = re.compile(r"#(\d+)\s+(\S+\.\w+)(?P<rest>.*)$")
    size_re = re.compile(r"(\d+)\s*KB\b", re.IGNORECASE)
    for line in output.splitlines():
        folder_match = folder_re.search(line)
        if folder_match:
            folder = folder_match.group(1)
            continue
        file_match = file_re.search(line)
        if file_match and folder:
            size_match = size_re.search(file_match.group("rest"))
            files.append(
                CameraFile(
                    index=int(file_match.group(1)),
                    name=file_match.group(2),
                    folder=folder,
                    size_kb=int(size_match.group(1)) if size_match else None,
                )
            )
    return files


def _format_index_ranges(indices: list[int]) -> str:
    """Compress ``[1,2,3,5,7,8]`` into gphoto2's ``1-3,5,7-8`` form."""
    if not indices:
        return ""
    ordered = sorted(set(indices))
    ranges: list[str] = []
    start = prev = ordered[0]
    for value in ordered[1:]:
        if value == prev + 1:
            prev = value
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = value
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(ranges)


def download_files(
    dest_dir: Path,
    files: list[CameraFile],
    *,
    port: str | None = None,
    on_file: Callable[[str], None] | None = None,
) -> None:
    """
    Download only the given camera files into dest_dir.

    Files are grouped by folder because gphoto2 file indices are per-folder.
    """
    if not files:
        return

    dest_dir.mkdir(parents=True, exist_ok=True)
    filename_pattern = str(dest_dir / "%f.%C")
    by_folder: dict[str, list[CameraFile]] = {}
    for item in files:
        by_folder.setdefault(item.folder, []).append(item)

    combined_output: list[str] = []
    failed = False
    for folder, folder_files in by_folder.items():
        indices = _format_index_ranges([item.index for item in folder_files])
        args = [
            "gphoto2",
            "--folder",
            folder,
            "--get-file",
            indices,
            "--filename",
            filename_pattern,
        ]
        if port:
            args.extend(["--port", port])
        log.info(
            "Downloading %d file(s) from %s (%s)",
            len(folder_files),
            folder,
            indices,
        )
        returncode, output = _stream(args, on_file=on_file)
        if output:
            combined_output.append(output)
        if returncode != 0:
            failed = True
            log.warning(
                "gphoto2 download exit %s for %s: %s",
                returncode,
                folder,
                clean_message(output),
            )

    if failed and not any(path.is_file() for path in dest_dir.rglob("*")):
        raise CameraError(
            clean_message("\n".join(combined_output)) or "Download failed with no files"
        )


def download_all(
    dest_dir: Path,
    *,
    port: str | None = None,
    skip_existing: bool = True,
    on_file: Callable[[str], None] | None = None,
) -> None:
    """
    Download all files from the camera into dest_dir.

    Prefer :func:`download_files` with a filtered listing for incremental imports.
    """
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

    returncode, output = _stream(args, on_file=on_file)
    if returncode != 0:
        stderr = clean_message(output)
        log.warning("gphoto2 download exit %s: %s", returncode, stderr)
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
        raise CameraError(clean_message(proc.stderr or proc.stdout or "delete failed"))
