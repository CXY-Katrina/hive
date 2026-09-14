#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
source "$HIVE_CLIENT_DEPS/activate.sh"
exec bash "${HIVE_TASK_OUTPUT_DIR:?}/verification/verify.sh"
