#!/usr/bin/env bash
# Runs after a successful import session.
# Environment variables provided by the importer:
#   IMPORT_SESSION_ID
#   IMPORT_DESTINATION
#   IMPORT_FILES_IMPORTED
#   IMPORT_FILES_SKIPPED
#   IMPORT_CAMERA_NAME
set -euo pipefail

echo "post_import: session=${IMPORT_SESSION_ID} imported=${IMPORT_FILES_IMPORTED} camera=${IMPORT_CAMERA_NAME}"

# Examples (uncomment / adapt):
#
# Immich CLI upload of the whole library (or a date folder):
#   immich upload --album "Camera Roll" "$IMPORT_DESTINATION"
#
# restic backup:
#   restic backup "$IMPORT_DESTINATION"
#
# Notify another service:
#   curl -fsS -X POST "$MY_WEBHOOK" -d "imported=${IMPORT_FILES_IMPORTED}"

exit 0
