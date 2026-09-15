#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
load_image_runtime server
cp /opt/hive-env/server/environment-report.json "$HIVE_OUTPUT_DIR/environment-report.json"
TASK_SCRIPTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TASK_PORT="${TASK_PORT:-18123}"
TASK_BENCHMARK="${TASK_BENCHMARK:-perf}"
TASK_CASE_YAML="${TASK_CASE_YAML:-$HIVE_SOURCE_DIR/tests/e2e/nightly/single_node/models/configs/Qwen3-30B-A3B-W8A8.yaml}"
# Parameters come from the attached nightly YAML; edit that file to change them.
assignments="$(python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" parameters --config "$TASK_CASE_YAML" --case "${TASK_CASE:-}" --benchmark "$TASK_BENCHMARK" --format shell)"
eval "$assignments"
python3 -c 'import socket,sys; s=socket.socket(); s.bind((sys.argv[1],int(sys.argv[2]))); s.close()' \
  "${HIVE_NODE0_IP:?}" "$TASK_PORT"
python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" server-command \
  --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
  --model-path "$(hive_resource model "$TASK_MODEL_NAME")" --host "$HIVE_NODE0_IP" --port "$TASK_PORT" \
  --output-dir "$HIVE_OUTPUT_DIR"
