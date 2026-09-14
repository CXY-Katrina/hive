#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
source "$HIVE_CLIENT_DEPS/activate.sh"
hive_case_parameters
python3 "$HIVE_SCRIPTS_DIR/nightly_cli.py" locate-results \
  --config "$HIVE_CASE_YAML" --case "$HIVE_CASE" --benchmark "$HIVE_BENCHMARK" \
  --manifest "${HIVE_TASK_OUTPUT_DIR:?}/client/manifest.json" --log "$HIVE_TASK_OUTPUT_DIR/client/aisbench.log" \
  --output-dir "$HIVE_TASK_OUTPUT_DIR/verification"
