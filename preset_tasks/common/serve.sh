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
load_image_runtime server
TASK_SCRIPTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$TASK_OUTPUT"
cp "$TASK_DEPS/environment-report.json" "$TASK_OUTPUT/environment-report.json"
assignments="$(python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" parameters --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" --format shell)"
eval "$assignments"
python3 -c 'import socket,sys; s=socket.socket(); s.bind((sys.argv[1],int(sys.argv[2]))); s.close()' \
  "$TASK_HOST" "$TASK_PORT"
python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" server-command \
  --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
  --model-path "$(hive_resource model "$TASK_MODEL_NAME")" --host "$TASK_HOST" --port "$TASK_PORT" \
  --output-dir "$TASK_OUTPUT"

TASK_COMMAND="$TASK_OUTPUT/server.json"
# Server-specific launch settings belong to the job, not environment creation.
export VLLM_WORKER_MULTIPROC_METHOD="${VLLM_WORKER_MULTIPROC_METHOD:-spawn}"
export VLLM_USE_MODELSCOPE="${VLLM_USE_MODELSCOPE:-True}"
export VLLM_ENGINE_READY_TIMEOUT_S="${VLLM_ENGINE_READY_TIMEOUT_S:-1800}"
export MAX_JOBS="${MAX_JOBS:-16}"
# Consume generated argv as data; all executable glue is visible here.
exec python3 - "$TASK_COMMAND" <<'PY'
import json, os, sys
with open(sys.argv[1]) as source:
    command = json.load(source)
os.environ.update(command["environment"])
argv = command["argv"]
if argv[:2] != ["vllm", "serve"]:
    raise ValueError("Expected native vllm serve argv")
os.execvpe(argv[0], argv, os.environ)
PY
