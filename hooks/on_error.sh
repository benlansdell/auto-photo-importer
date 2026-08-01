#!/usr/bin/env bash
# Runs when an import session fails.
# Environment:
#   IMPORT_SESSION_ID
#   IMPORT_ERROR
#   IMPORT_CAMERA_NAME
set -euo pipefail

echo "on_error: session=${IMPORT_SESSION_ID:-none} camera=${IMPORT_CAMERA_NAME:-unknown}"
echo "on_error: ${IMPORT_ERROR:-unknown error}" >&2

# Example:
#   curl -fsS -X POST "$ALERT_WEBHOOK" -d "error=${IMPORT_ERROR}"

exit 0
