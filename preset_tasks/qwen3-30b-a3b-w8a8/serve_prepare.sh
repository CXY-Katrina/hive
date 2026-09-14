#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
source "$HIVE_SERVER_DEPS/activate.sh"
hive_case_parameters
python3 -c 'import socket,sys; s=socket.socket(); s.bind((sys.argv[1],int(sys.argv[2]))); s.close()' \
  "${HIVE_HOST_IP:?}" "${HIVE_PORT:?}"
python3 "$HIVE_SCRIPTS_DIR/nightly_cli.py" server-command \
  --config "$HIVE_CASE_YAML" --case "$HIVE_CASE" --benchmark "$HIVE_BENCHMARK" \
  --model-path "$(hive_resource model "$HIVE_MODEL_NAME")" --host "$HIVE_HOST_IP" --port "$HIVE_PORT" \
  --output-dir "${HIVE_TASK_OUTPUT_DIR:?}/serve"
