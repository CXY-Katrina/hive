#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
wheelhouse="$(hive_resource package python-wheelhouse 2>/dev/null || true)"
if [ -n "$wheelhouse" ]; then
  test -d "$wheelhouse"
  export PIP_FIND_LINKS="file://$wheelhouse" PIP_NO_INDEX=1
fi
python3 "$HIVE_SCRIPTS_DIR/nightly_environment.py" \
  --source-root "$HIVE_SOURCE_DIR" --source-commit "${HIVE_ASCEND_SHA:?}" \
  --role client --vllm-sha "${HIVE_VLLM_SHA:?}" --dep-dir "$HIVE_CLIENT_DEPS" --runtime-mode image-reuse \
  --benchmark-source "$(hive_resource package aisbench-source)" --install-client-dependencies
