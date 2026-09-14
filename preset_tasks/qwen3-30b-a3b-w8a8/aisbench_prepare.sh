#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
source "$HIVE_CLIENT_DEPS/activate.sh"
hive_case_parameters
python3 "$HIVE_SCRIPTS_DIR/nightly_cli.py" prepare \
  --config "$HIVE_CASE_YAML" --case "$HIVE_CASE" --benchmark "$HIVE_BENCHMARK" \
  --benchmark-home "$HIVE_CLIENT_DEPS/benchmark" \
  --model-path "$(hive_resource model "$HIVE_MODEL_NAME")" --dataset-path "$(hive_resource dataset "$HIVE_DATASET_NAME")" \
  --host "${HIVE_JOB_SERVE_NODE0_HOST:?}" --port "${HIVE_JOB_SERVE_NODE0_PORT:?}" \
  --output-dir "${HIVE_TASK_OUTPUT_DIR:?}/client"
