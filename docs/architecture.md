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

The Compose bind mount `./db:/db` keeps `/db/imports.sqlite3` outside the
container. Rebuilding or replacing the container does not remove it. Copy the
database with the photo library when moving the service to another machine.

## USB passthrough (UGREEN / Docker)

The compose file mounts `/dev/bus/usb` and runs privileged. On some NAS firmwares you may need:

- Host Docker with device cgroup support
- Camera set to **USB connection: USB CARD READER** or **USB TETHER SHOOTING / PTP** (X-T3: Setup → Connection Setting → USB Connection Mode → **USB CARD READER** or PTP-compatible mode)

If `gphoto2 --auto-detect` inside the container shows nothing while `lsusb` on the host shows the Fujifilm device, check that the host is not claiming the device via gvfs/mtp-probe.
