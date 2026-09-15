#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
load_image_runtime client
TASK_CLIENT_DEPS=/opt/hive-env/client
export PATH="$TASK_CLIENT_DEPS/venv/bin:$PATH"
export PYTHONPATH="$TASK_CLIENT_DEPS/benchmark${PYTHONPATH:+:$PYTHONPATH}"
TASK_SCRIPTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TASK_PORT="${TASK_PORT:-18123}"
TASK_BENCHMARK="${TASK_BENCHMARK:-perf}"
TASK_CASE_YAML="${TASK_CASE_YAML:-$HIVE_SOURCE_DIR/tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml}"
# Parameters come from the attached nightly YAML; edit that file to change them.
assignments="$(python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" parameters --config "$TASK_CASE_YAML" --case "${TASK_CASE:-}" --benchmark "$TASK_BENCHMARK" --format shell)"
eval "$assignments"
exec python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" verify \
  --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
  --result-json "$HIVE_OUTPUT_DIR/result.json" --result-csv "$HIVE_OUTPUT_DIR/result.csv" \
  --output-file "$HIVE_OUTPUT_DIR/verification.json"
