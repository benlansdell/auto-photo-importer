#!/usr/bin/env bash
# Runs after a successful import session.
# Environment variables provided by the importer:
#   IMPORT_SESSION_ID
#   IMPORT_DESTINATION
#   IMPORT_FILES_IMPORTED
#   IMPORT_FILES_SKIPPED
#   IMPORT_CAMERA_NAME
#   IMPORT_MANIFEST      file listing the absolute paths imported in THIS session
set -euo pipefail

echo "post_import: session=${IMPORT_SESSION_ID} imported=${IMPORT_FILES_IMPORTED} camera=${IMPORT_CAMERA_NAME}"

# --- Google Photos: upload only this session's JPEGs (never backfills) ---
# gphotos-uploader-cli uploads a folder, so this stages symlinks to the new
# JPEGs in an outbox and points the CLI at that. The library itself is never
# scanned, so files imported before this was set up stay local.
GPHOTOS_CONFIG_DIR="${GPHOTOS_CONFIG_DIR:-/gphotos}"
GPHOTOS_OUTBOX="${GPHOTOS_OUTBOX:-${GPHOTOS_CONFIG_DIR}/outbox}"

stage_new_jpegs() {
  local manifest="$1" staged=0 file rel link
  shopt -s nocasematch
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    case "$file" in
      *.jpg | *.jpeg) ;;
      *) continue ;;
    esac
    [[ -f "$file" ]] || continue

    # Mirror the YYYY/MM/DD path so album templates still see the date folders.
    rel="${file#"${IMPORT_DESTINATION}"/}"
    link="${GPHOTOS_OUTBOX}/${rel}"
    mkdir -p "$(dirname "$link")"
    ln -sfn "$file" "$link"
    staged=$((staged + 1))
  done <"$manifest"
  echo "$staged"
}

upload_to_google_photos() {
  if [[ ! -f "${GPHOTOS_CONFIG_DIR}/config.hjson" ]]; then
    echo "post_import: no ${GPHOTOS_CONFIG_DIR}/config.hjson — Google Photos upload disabled"
    return 0
  fi
  if [[ -z "${GPHOTOS_CLI_TOKENSTORE_KEY:-}" ]]; then
    echo "post_import: GPHOTOS_CLI_TOKENSTORE_KEY is unset — skipping Google Photos upload" >&2
    return 0
  fi
  if ! command -v gphotos-uploader-cli >/dev/null 2>&1; then
    echo "post_import: gphotos-uploader-cli not installed — skipping Google Photos upload" >&2
    return 0
  fi

  mkdir -p "$GPHOTOS_OUTBOX"

  local staged=0
  if [[ -n "${IMPORT_MANIFEST:-}" && -f "${IMPORT_MANIFEST}" ]]; then
    staged=$(stage_new_jpegs "$IMPORT_MANIFEST")
  fi

  # Anything left from a previous failed run is retried alongside the new files.
  local pending
  pending=$(find "$GPHOTOS_OUTBOX" -type l | wc -l | tr -d ' ')
  if [[ "$pending" -eq 0 ]]; then
    echo "post_import: no new JPEGs to upload"
    return 0
  fi

  echo "post_import: uploading ${pending} JPEG(s) to Google Photos (${staged} new this session)…"
  gphotos-uploader-cli --config "$GPHOTOS_CONFIG_DIR" push

  # Only clear the outbox once push succeeded, so failures retry next import.
  find "$GPHOTOS_OUTBOX" -type l -delete
  find "$GPHOTOS_OUTBOX" -mindepth 1 -type d -empty -delete
  echo "post_import: Google Photos upload complete"
}

upload_to_google_photos

exit 0
