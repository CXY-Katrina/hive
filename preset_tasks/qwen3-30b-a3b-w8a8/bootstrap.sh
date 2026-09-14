#!/usr/bin/env bash
set -euo pipefail
# The platform passes the selected image and this instance's container name.
: "${1:?image}" "${2:?container name}"
exec bash "${TASK_BOOTSTRAP_SCRIPT:-/mnt/share/c00814587/start-docker-A3.sh}" "$1" "$2"
