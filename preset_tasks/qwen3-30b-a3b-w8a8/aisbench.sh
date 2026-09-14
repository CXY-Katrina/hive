#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
source "$HIVE_CLIENT_DEPS/activate.sh"
cd /var/tmp
# Keep native AISBench in the foreground; pipefail preserves its exit code.
bash "${HIVE_TASK_OUTPUT_DIR:?}/client/benchmark.sh" 2>&1 | tee "$HIVE_TASK_OUTPUT_DIR/client/aisbench.log"
