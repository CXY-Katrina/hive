#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
source "$HIVE_SERVER_DEPS/activate.sh"
cd /var/tmp
exec bash "${HIVE_TASK_OUTPUT_DIR:?}/serve/server.sh"
