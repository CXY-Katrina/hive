#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
python3 "$HIVE_SCRIPTS_DIR/nightly_environment.py" \
  --source-root "$HIVE_SOURCE_DIR" --source-commit "${HIVE_ASCEND_SHA:?}" \
  --role server --vllm-sha "${HIVE_VLLM_SHA:?}" --dep-dir "$HIVE_SERVER_DEPS" --runtime-mode image-reuse
