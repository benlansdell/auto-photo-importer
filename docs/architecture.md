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

To avoid re-transferring the whole card on every plug, the importer first
compares the camera's file listing against import history using
**filename + size** taken from `gphoto2 --list-files`. Matches are skipped
without downloading; everything else is transferred and then judged by SHA-256.

Note the units: `gphoto2` prints sizes in **KB** (`rd 12809 KB`) while the
database stores **bytes**. `selection._kb_variants` compares a stored byte size
against both floor and ceiling KB division, since gphoto2 builds differ in which
they use. The size is only read from the field explicitly labelled `KB` —
optional resolution (`5568x3712`) and trailing epoch columns vary by camera, and
guessing at column position silently produced non-matching sizes.

Filename and size together are cheap and per-file exact:

- After a camera filename-counter rollover, a reused `DSCF0001.RAF` has a
  different size, so it is correctly treated as new.
- A file missing from history because an earlier run was interrupted is
  downloaded, even if higher-numbered files were imported later.

An earlier design stored a single per-camera high-water sequence number and
skipped everything at or below it. That was dropped: a maximum assumes the
history beneath it is complete, so any gap left by an interrupted run became a
permanent silent skip. `imports.source_filename` records the name the file had
on the camera, so library-side renames (collision suffixes) do not cause repeat
downloads.

## Google Photos (optional)

`hooks/post_import.sh` uploads **only the JPEGs from the session that just ran**
— the library is never scanned, so nothing is backfilled.

`run_post_import` writes the session's destination paths to a manifest file and
passes it as `IMPORT_MANIFEST` (a file rather than argv, so a full card cannot
overflow the argument list). The hook filters that list to JPEG, symlinks each
one into `/gphotos/outbox` mirroring the `YYYY/MM/DD` layout, and runs
`gphotos-uploader-cli --config /gphotos push` against the outbox.

Symlinks are deleted only after `push` exits 0, so a failed or interrupted
upload is retried on the next import. `DeleteAfterUpload` must stay `false` in
`config.hjson`: the outbox entries point into the real library.

Config and the encrypted OAuth token live in the host `./gphotos` bind mount
(`SecretsBackendType: file` plus `GPHOTOS_CLI_TOKENSTORE_KEY`, since a NAS has no
desktop keyring). See the README for Cloud Console and `auth` steps.

## Name collisions

When a destination name is taken, the organizer compares content before doing
anything: if an existing file (or an existing `_1`, `_2` … sibling) has the same
SHA-256, the staged copy is dropped and the item is marked `already_present`
rather than becoming a `_1` duplicate. Only a genuinely different photo with the
same name gets a suffix.

`already_present` files are counted as skipped and are *also* recorded in the
database, so a rebuilt or restored database re-learns the library it is pointed
at instead of duplicating it. `scripts/reconcile_library.py` performs the same
repair in bulk for damage done before this existed.

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
