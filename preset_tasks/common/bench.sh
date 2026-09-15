#!/usr/bin/env bash
set -euo pipefail
TASK_CASE_YAML="${1:?nightly YAML path}"
TASK_CASE="${2:?case name}"
TASK_BENCHMARK="${3:?benchmark type}"
TASK_HOST="${4:?server IP}"
TASK_PORT="${5:?server port}"
TASK_DEPS="${6:?dependency directory}"
TASK_OUTPUT="${7:?output directory}"
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
TASK_CLIENT_DEPS="$TASK_DEPS"
load_image_runtime client
export PATH="$TASK_CLIENT_DEPS/venv/bin:$PATH"
export PYTHONPATH="$TASK_CLIENT_DEPS/benchmark${PYTHONPATH:+:$PYTHONPATH}"
TASK_SCRIPTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$TASK_OUTPUT"
cp "$TASK_DEPS/environment-report.json" "$TASK_OUTPUT/environment-report.json"
assignments="$(python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" parameters --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" --format shell)"
eval "$assignments"
python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" prepare \
  --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
  --benchmark-home "$TASK_CLIENT_DEPS/benchmark" \
  --model-path "$(hive_resource model "$TASK_MODEL_NAME")" --dataset-path "$(hive_resource dataset "$TASK_DATASET_NAME")" \
  --host "$TASK_HOST" --port "$TASK_PORT" --output-dir "$TASK_OUTPUT"

TASK_MANIFEST="$TASK_OUTPUT/manifest.json"
TASK_LOG="$TASK_OUTPUT/aisbench.log"
# The official CLI runs in the foreground; pipefail preserves its exit code.
python3 - "$TASK_MANIFEST" <<'PY' 2>&1 | tee "$TASK_LOG"
import json, sys
with open(sys.argv[1]) as source:
    argv = json.load(source)["argv"]
if argv[0] != "ais_bench":
    raise ValueError("Expected native AISBench argv")
sys.argv = argv
from ais_bench.benchmark.cli.main import main
sys.exit(main())
PY
