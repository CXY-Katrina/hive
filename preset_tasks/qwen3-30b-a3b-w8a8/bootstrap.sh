#!/usr/bin/env bash
set -euo pipefail
# HIVE_IMAGE is the platform's resolved local image identity.
: "${HIVE_IMAGE:?}" "${HIVE_CONTAINER_NAME:?}"
HIVE_BOOTSTRAP_SCRIPT="${HIVE_BOOTSTRAP_SCRIPT:-/mnt/share/c00814587/start-docker-A3.sh}"
exec bash "$HIVE_BOOTSTRAP_SCRIPT" "$HIVE_IMAGE" "$HIVE_CONTAINER_NAME"
