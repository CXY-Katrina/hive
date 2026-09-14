#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/install-server.sh"
source "$(dirname -- "${BASH_SOURCE[0]}")/case-common.sh"
case "${1:-}" in
  serve-prepare)
    load_server_runtime
    copy_environment_report "$TASK_SERVER_DEPS"
    case_parameters
    python3 -c 'import socket,sys; s=socket.socket(); s.bind((sys.argv[1],int(sys.argv[2]))); s.close()' \
      "${HIVE_NODE0_IP:?}" "$TASK_PORT"
    python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" server-command \
      --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
      --model-path "$(hive_resource model "$TASK_MODEL_NAME")" --host "$HIVE_NODE0_IP" --port "$TASK_PORT" \
      --output-dir "$HIVE_OUTPUT_DIR"
    ;;
  serve)
    load_server_runtime
    # Server-specific launch settings belong to the job, not environment creation.
    export VLLM_WORKER_MULTIPROC_METHOD="${VLLM_WORKER_MULTIPROC_METHOD:-spawn}"
    export VLLM_USE_MODELSCOPE="${VLLM_USE_MODELSCOPE:-True}"
    export VLLM_ENGINE_READY_TIMEOUT_S="${VLLM_ENGINE_READY_TIMEOUT_S:-1800}"
    export VLLM_CI_RUNNER="${VLLM_CI_RUNNER:-linux-aarch64-nightly-a3-4}"
    export MAX_JOBS="${MAX_JOBS:-16}"
    # Consume generated argv as data; all executable glue is visible here.
    exec python3 - "$HIVE_OUTPUT_DIR/server.json" <<'PY'
import json, os, sys
with open(sys.argv[1]) as source:
    command = json.load(source)
os.environ.update(command["environment"])
argv = command["argv"]
if argv[:2] != ["vllm", "serve"]:
    raise ValueError("Expected native vllm serve argv")
os.execvpe(argv[0], argv, os.environ)
PY
    ;;
  ready)
    exec curl --fail --silent --show-error --retry 300 --retry-all-errors --retry-delay 2 \
      --retry-max-time 900 --connect-timeout 2 --max-time 5 "http://${HIVE_NODE0_IP:?}:$TASK_PORT/health"
    ;;
  *) echo "Expected serve-prepare | serve | ready" >&2; exit 2 ;;
esac
