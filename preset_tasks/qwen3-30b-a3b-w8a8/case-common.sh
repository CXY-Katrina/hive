#!/usr/bin/env bash
# Editable task settings. These are preset settings, not platform variables.
TASK_PORT="${TASK_PORT:-18123}"
TASK_BENCHMARK="${TASK_BENCHMARK:-perf}"
: "${HIVE_SOURCE_DIR:?Current upstream checkout is required}"
TASK_SCRIPTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TASK_CASE_YAML="${TASK_CASE_YAML:-$HIVE_SOURCE_DIR/tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml}"

case_parameters() {
  local assignments
  assignments="$(python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" parameters \
    --config "$TASK_CASE_YAML" --case "${TASK_CASE:-}" --benchmark "$TASK_BENCHMARK" --format shell)"
  # Only four fixed TASK_ assignments, with values quoted by shlex.
  eval "$assignments"
}

copy_environment_report() {
  : "${HIVE_OUTPUT_DIR:?Platform job output directory is required}"
  local deps="$1"
  mkdir -p "$HIVE_OUTPUT_DIR"
  cp -- "$deps/environment-report.json" "$HIVE_OUTPUT_DIR/environment-report.json"
}
