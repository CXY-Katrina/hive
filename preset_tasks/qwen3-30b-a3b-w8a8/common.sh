#!/usr/bin/env bash
set -euo pipefail
# Shared paths and YAML parameter discovery; sourcing this file starts no workload.
: "${HIVE_SOURCE_DIR:?Hive must provide the current upstream checkout}"
HIVE_SCRIPTS_DIR="$HIVE_SOURCE_DIR/hive_presets/qwen3-30b-a3b-w8a8"
HIVE_SERVER_DEPS="${HIVE_SERVER_DEPS:-/opt/hive-nightly-deps/server}"
HIVE_CLIENT_DEPS="${HIVE_CLIENT_DEPS:-/opt/hive-nightly-deps/client}"
HIVE_CASE_YAML="${HIVE_CASE_YAML:-$HIVE_SOURCE_DIR/tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml}"
if [ -n "${HIVE_TASK_ID:-}" ]; then
  HIVE_TASK_OUTPUT_DIR="${HIVE_TASK_OUTPUT_DIR:-/var/tmp/hive-nightly/$HIVE_TASK_ID}"
fi
hive_case_parameters() {
  test -f "$HIVE_CASE_YAML"
  local assignments
  assignments="$(python3 "$HIVE_SCRIPTS_DIR/nightly_cli.py" parameters \
    --config "$HIVE_CASE_YAML" --case "${HIVE_CASE:-}" --benchmark "${HIVE_BENCHMARK:-perf}" --format shell)"
  # The CLI emits only four fixed variable names with shlex-quoted values.
  eval "$assignments"
}
