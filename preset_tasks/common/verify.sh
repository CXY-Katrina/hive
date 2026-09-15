#!/usr/bin/env bash
set -euo pipefail
TASK_CASE_YAML="${1:?nightly YAML path}"
TASK_CASE="${2:?case name}"
TASK_BENCHMARK="${3:?benchmark type}"
TASK_CLIENT_DEPS="${4:?dependency directory}"
TASK_OUTPUT="${5:?output directory}"
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
load_image_runtime client
export PATH="$TASK_CLIENT_DEPS/venv/bin:$PATH"
export PYTHONPATH="$TASK_CLIENT_DEPS/benchmark${PYTHONPATH:+:$PYTHONPATH}"
TASK_SCRIPTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$TASK_OUTPUT"
assignments="$(python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" parameters --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" --format shell)"
eval "$assignments"
exec python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" verify \
  --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
  --result-json "$TASK_OUTPUT/result.json" --result-csv "$TASK_OUTPUT/result.csv" \
  --output-file "$TASK_OUTPUT/verification.json"
