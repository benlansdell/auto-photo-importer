# Photo Importer

Automatic Fujifilm X-T3 (PTP) photo ingest for a Linux NAS — Docker service with gphoto2, SQLite-backed SHA-256 duplicate detection, date-based library folders, and a small operational dashboard.

Plug in the camera → files land under `/photos/YYYY/MM/DD/` → optional hooks (Immich, restic, Discord).

## Features

- Auto-detect Fujifilm cameras via `gphoto2`
- State machine watcher (no double-import on flaky USB)
- RAW+JPEG pairs kept together
- Organize by EXIF `DateTimeOriginal` into `YYYY/MM/DD`
- SHA-256 duplicate detection with persistent SQLite import history
- Optional post-move SHA-256 verification
- Optional delete-from-camera after verified import
- Post-import / on-error hook scripts + webhook notifications
- FastAPI dashboard on port **8080** (status, logs, history, Import now / Eject)
- Dry-run mode, retries, structured logging, Docker healthcheck

## Quick start (UGREEN / Docker)

```bash
git clone <this-repo> photo-importer
cd photo-importer
cp config.example.yaml config/config.yaml
# Edit destination mount in docker-compose.yml to match your NAS share
docker compose up -d --build
```

Open `http://<nas-ip>:8080`.

The database is stored in `./db/imports.sqlite3` on the Docker host through a
bind mount. It survives container restarts, replacements, and image rebuilds.
Copy the `db` directory alongside the photo library when moving to another host.

### Compose volume

Default host path is `/volume1/photos` — change it in `docker-compose.yml`:

```yaml
volumes:
  - /path/to/your/photos:/photos
```

### Camera USB mode (X-T3)

On the camera: **Setup → Connection Setting → USB Connection Mode** → use a PTP-compatible mode (often **USB CARD READER** or PC remote / tether, depending on firmware). Confirm on the NAS:

```bash
docker compose exec importer gphoto2 --auto-detect
```

You should see `Fujifilm X-T3`.

## Configuration

See [`config.example.yaml`](config.example.yaml). Important knobs:

| Key | Default | Meaning |
|-----|---------|---------|
| `camera_name` | `Fujifilm` | Substring match on `gphoto2 --auto-detect` |
| `destination` | `/photos` | Library root inside the container |
| `delete_after_import` | `false` | Wipe camera only after verified import |
| `verify_hashes` | `true` | SHA-256 before / after move |
| `dry_run` | `false` | List only, no writes |
| `folder_layout` | `ymd` | `2026/07/31` (or `ym_ymd` → `2026/2026-07-31`) |
| `notifications.webhook_url` | `null` | Discord / generic webhook |

## Hooks

Scripts in `./hooks` are mounted at `/hooks`:

- `post_import.sh` — success (env: `IMPORT_SESSION_ID`, `IMPORT_DESTINATION`, …)
- `on_error.sh` — failure

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
ruff check src tests
```

Run locally without Docker (needs `gphoto2` + `exiftool` on the host):

```bash
cp config.example.yaml config/config.yaml
# point destination/staging/database/log paths at local dirs
python -m photo_importer --config config/config.yaml
```

## Architecture

See [docs/architecture.md](docs/architecture.md).

## Safety notes

- Keep `delete_after_import: false` until you trust the pipeline.
- Duplicates are skipped by content hash, so camera filename reuse is safe.
- Keep `./db` backed up with the photo library; it is the persistent import index.
- The service waits for unplug after each session before accepting another import.
