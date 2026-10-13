# Photo Importer

Automatic Fujifilm X-T3 (PTP) photo ingest for a Linux NAS — Docker service with gphoto2, SQLite-backed SHA-256 duplicate detection, date-based library folders, and a small operational dashboard.

Plug in the camera → files land under `/photos/YYYY/MM/DD/` → optional hooks (Immich, restic, Discord).

## Features

- Auto-detect Fujifilm cameras via `gphoto2`
- State machine watcher (no double-import on flaky USB)
- RAW+JPEG pairs kept together
- Organize by EXIF `DateTimeOriginal` into `YYYY/MM/DD`
- SHA-256 duplicate detection with persistent SQLite import history
- Re-plugs skip files already imported (matched on camera filename + byte size from the
  listing, so nothing is transferred twice and no gap is silently missed)
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

### USB passthrough must be a bind mount

`/dev/bus/usb` is mounted as a volume, not declared under `devices:`. Docker
resolves `devices:` when the container is created, so a camera plugged in later
never gets a device node inside the container and every transfer fails with
`-52: Could not find the requested device on the USB port` even though
`--auto-detect` lists the camera. See
[docs/architecture.md](docs/architecture.md#error--52-could-not-find-the-requested-device-on-the-usb-port).

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

`post_import.sh` uploads JPEGs to Google Photos when `./gphotos/config.hjson` is
present and `GPHOTOS_CLI_TOKENSTORE_KEY` is set (see below).

## Google Photos

After each successful import, the hook uploads **only the JPEGs from that
session** with [`gphotos-uploader-cli`](https://github.com/gphotosuploader/gphotos-uploader-cli).

- **No backfill.** The existing library is never scanned or uploaded. Only files
  imported after setup are sent, so Google Photos holds a partial library by design.
- **JPEG only.** RAF / RAW stays on the NAS.
- The importer writes the session's paths to a manifest; the hook symlinks the
  JPEGs into `./gphotos/outbox` (mirroring `YYYY/MM/DD`) and points the CLI there.
- Links are removed only after a successful `push`, so a failed upload is retried
  on the next import instead of being lost.

### One-time setup

1. In [Google Cloud Console](https://console.cloud.google.com/):
   - Create a project
   - Enable **Google Photos Library API**
   - Configure the OAuth consent screen
   - Create OAuth client ID → application type **Desktop app**
2. Copy the example config and fill in credentials + your Google account email:

   ```bash
   cp gphotos/config.hjson.example gphotos/config.hjson
   # edit ClientID, ClientSecret, Account
   ```

3. Set a passphrase in `docker-compose.yml` (uncomment and change it):

   ```yaml
   environment:
     GPHOTOS_CLI_TOKENSTORE_KEY: "a-long-random-passphrase"
   ```

4. Rebuild so the CLI is in the image, then authenticate once (needs a browser):

   ```bash
   docker compose up -d --build
   docker compose exec importer \
     gphotos-uploader-cli --config /gphotos auth
   ```

   Follow the printed URL, approve access, then paste the code back if asked.
   The encrypted token is stored under `./gphotos/` (gitignored).

   **Headless NAS tip:** run `auth` on a laptop with the same `config.hjson` and
   `GPHOTOS_CLI_TOKENSTORE_KEY`, then copy the whole `gphotos/` folder to the NAS.

After that, every successful camera import uploads that session's new JPEGs
automatically. Disable by removing `gphotos/config.hjson` or unsetting
`GPHOTOS_CLI_TOKENSTORE_KEY`.

### If you ever *do* want to send older photos

Nothing is uploaded retroactively. To send a specific day by hand, symlink it into
the outbox and push:

```bash
docker compose exec importer bash -c \
  'mkdir -p /gphotos/outbox/2026/08/09 && \
   ln -sfn /photos/2026/08/09/*.JPG /gphotos/outbox/2026/08/09/ && \
   gphotos-uploader-cli --config /gphotos push && \
   find /gphotos/outbox -type l -delete'
```

## Why is it downloading files I already have?

`scripts/explain_selection.py` answers that against the connected camera. It is
read-only — it lists and compares, then stops:

```bash
docker compose exec importer python /app/scripts/explain_selection.py
```

It prints the raw `gphoto2 --list-files` lines, how many of them a size could be
read from, what the database knows, and a per-file verdict such as
`skip: filename + size match history` or
`download: size 4123 KB does not match history [4124] KB`.

If it reports that no sizes were parsed while the raw lines clearly show one, the
size label differs from `KB` on your camera and `camera.parse_file_list` needs to
handle that format. In the meantime the importer falls back to filename-only
matching, which still avoids re-downloading the card.

## Recovering a library that got duplicated

If the import database was lost, replaced, or out of sync, earlier runs could
copy photos the library already had as `DSCF0001_1.JPG`. `scripts/reconcile_library.py`
removes those and re-indexes the library so imports skip them from then on.

It reports without changing anything unless you pass `--apply`:

```bash
# 1. See what it would do (hashes the library; no writes)
docker compose exec importer python /app/scripts/reconcile_library.py

# 2. Apply once the report looks right
docker compose exec importer python /app/scripts/reconcile_library.py --apply

# Index only, keep every file on disk
docker compose exec importer python /app/scripts/reconcile_library.py --apply --no-delete
```

Of files sharing a SHA-256 it keeps one copy, preferring the un-suffixed name,
and records each survivor under the filename it had on the camera. Back up
`./db` and the library before the `--apply` run.

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
