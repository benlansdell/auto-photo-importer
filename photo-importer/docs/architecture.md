# Architecture

```
XT3 (USB / PTP)
      │
Docker container
  ├── watcher.py      poll / state machine
  ├── importer.py     session orchestration
  ├── camera.py       gphoto2 wrapper
  ├── verifier.py     SHA-256
  ├── organizer.py    EXIF date folders
  ├── database.py     persistent SQLite import history
  ├── hooks.py        post_import / on_error / webhook
  └── web/            FastAPI dashboard :8080
      │
 /photos/YYYY/MM/DD/
```

## State machine

`WAITING → CAMERA_FOUND → IMPORTING → VERIFYING → ORGANIZING → POST_PROCESS → WAIT_FOR_UNPLUG → WAITING`

Errors transition to `ERROR`, then still wait for unplug before returning to `WAITING` so a flaky USB link cannot double-import.

## Duplicate detection

Every downloaded file's SHA-256 is checked against SQLite before it is moved
into the library. Successful imports and sessions are recorded for duplicate
detection and dashboard history.

A per-camera **high-water mark** (highest `DSCF####` sequence successfully
handled) is also stored. On later plugs the importer lists the camera, skips
everything at or below that mark, and only downloads newer files. SHA-256 remains
the durable safety net for anything that does get transferred. If the camera's
filename counter resets (max on camera < watermark), the mark is cleared and a
full pass runs again.

The Compose bind mount `./db:/db` keeps `/db/imports.sqlite3` outside the
container. Rebuilding or replacing the container does not remove it. Copy the
database with the photo library when moving the service to another machine.

## USB passthrough (UGREEN / Docker)

The compose file mounts `/dev/bus/usb` and runs privileged. On some NAS firmwares you may need:

- Host Docker with device cgroup support
- Camera set to **USB connection: USB CARD READER** or **USB TETHER SHOOTING / PTP** (X-T3: Setup → Connection Setting → USB Connection Mode → **USB CARD READER** or PTP-compatible mode)

If `gphoto2 --auto-detect` inside the container shows nothing while `lsusb` on the host shows the Fujifilm device, check that the host is not claiming the device via gvfs/mtp-probe.

## Error -52: "Could not find the requested device on the USB port"

The usual cause is a `devices:` entry in Compose:

```yaml
devices:
  - /dev/bus/usb:/dev/bus/usb   # wrong for hot-plugged cameras
```

Docker resolves `devices:` when the container is created and copies the matching
nodes into the container's private `/dev`. A camera plugged in later never gets a
node there. `gphoto2 --auto-detect` still lists it, because libusb enumerates
through `/sys`, but every attempt to open the camera fails with error -52. Use a
bind mount so the host's live directory is shared:

```yaml
volumes:
  - /dev/bus/usb:/dev/bus/usb
```

Confirm from inside the container, with the camera attached:

```bash
docker compose exec importer gphoto2 --auto-detect   # e.g. usb:003,004
docker compose exec importer ls -l /dev/bus/usb/003/004
```

If the port is listed but the node is missing, the importer logs that path along
with this explanation instead of only reporting -52.

A second, less common cause is re-enumeration: Fujifilm bodies can reappear at a
new USB address after a PTP session closes, making a previously captured port
stale. The importer re-runs `--auto-detect` before every camera operation,
falls back to letting `gphoto2` choose the single attached camera, pauses
`camera_settle_seconds` before each download attempt, and reads the camera
summary after the download rather than before it.
