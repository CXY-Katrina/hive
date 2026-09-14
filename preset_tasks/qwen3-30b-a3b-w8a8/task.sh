#!/usr/bin/env bash
set -euo pipefail

# Editable task settings. These are preset settings, not platform variables.
TASK_PORT="${TASK_PORT:-18123}"
TASK_SERVER_DEPS="${TASK_SERVER_DEPS:-/opt/hive-nightly-deps/server}"
TASK_CLIENT_DEPS="${TASK_CLIENT_DEPS:-/opt/hive-nightly-deps/client}"
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

load_runtime() {
  local role="$1" cann_loaded=0 cann_env path source_root filtered=""
  local paths=()
  # Never import a requested checkout over the image's installed NPU binaries.
  source_root="$(cd -- "$HIVE_SOURCE_DIR" && pwd -P)"
  IFS=: read -r -a paths <<< "${PYTHONPATH:-}"
  for path in "${paths[@]}"; do
    test -n "$path" || continue
    path="$(cd -- "$path" 2>/dev/null && pwd -P)" || continue
    case "$path" in "$source_root"|"$source_root"/*) continue ;; esac
    filtered="${filtered:+$filtered:}$path"
  done
  unset PYTHONHOME
  export PYTHONPATH="$filtered"
  export PYTHONNOUSERSITE=1
  cd /var/tmp
  set +u
  for cann_env in "${TASK_CANN_ENV:-}" /usr/local/Ascend/cann/set_env.sh \
      /usr/local/Ascend/ascend-toolkit/set_env.sh /usr/local/Ascend/ascend-toolkit/latest/set_env.sh; do
    if test -n "$cann_env" && test -f "$cann_env"; then
      source "$cann_env" >/dev/null
      cann_loaded=1
      break
    fi
  done
  if test "$cann_loaded" != 1; then echo "CANN environment script missing" >&2; exit 1; fi
  if test -f /usr/local/Ascend/nnal/atb/set_env.sh; then
    source /usr/local/Ascend/nnal/atb/set_env.sh >/dev/null
  fi
  set -u
  export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"
  if test "$role" = client; then
    test -x "$TASK_CLIENT_DEPS/venv/bin/python3"
    export PATH="$TASK_CLIENT_DEPS/venv/bin:$PATH"
    export PYTHONPATH="$TASK_CLIENT_DEPS/benchmark${PYTHONPATH:+:$PYTHONPATH}"
  fi
}

copy_environment_report() {
  : "${HIVE_OUTPUT_DIR:?Platform job output directory is required}"
  local deps="$TASK_SERVER_DEPS"
  if test "$1" = client; then deps="$TASK_CLIENT_DEPS"; fi
  mkdir -p "$HIVE_OUTPUT_DIR"
  cp -- "$deps/environment-report.json" "$HIVE_OUTPUT_DIR/environment-report.json"
}

# Installation scripts source only the shared runtime functions above.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then return; fi

case "${1:-}" in
  verify-env)
    case "${2:-}" in
      server) report="$TASK_SERVER_DEPS/environment-report.json" ;;
      client) report="$TASK_CLIENT_DEPS/environment-report.json" ;;
      *) echo "Select server or client" >&2; exit 2 ;;
    esac
    test -s "$report"
    cat "$report"
    ;;
  serve-prepare)
    load_runtime server
    copy_environment_report server
    case_parameters
    python3 -c 'import socket,sys; s=socket.socket(); s.bind((sys.argv[1],int(sys.argv[2]))); s.close()' \
      "${HIVE_NODE0_IP:?}" "$TASK_PORT"
    python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" server-command \
      --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
      --model-path "$(hive_resource model "$TASK_MODEL_NAME")" --host "$HIVE_NODE0_IP" --port "$TASK_PORT" \
      --output-dir "$HIVE_OUTPUT_DIR"
    ;;
  serve)
    load_runtime server
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
  bench-prepare)
    load_runtime client
    copy_environment_report client
    case_parameters
    python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" prepare \
      --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
      --benchmark-home "$TASK_CLIENT_DEPS/benchmark" \
      --model-path "$(hive_resource model "$TASK_MODEL_NAME")" --dataset-path "$(hive_resource dataset "$TASK_DATASET_NAME")" \
      --host "${HIVE_NODE0_IP:?}" --port "$TASK_PORT" --output-dir "$HIVE_OUTPUT_DIR"
    ;;
  bench)
    load_runtime client
    # The official CLI runs in the foreground; pipefail preserves its exit code.
    python3 - "$HIVE_OUTPUT_DIR/manifest.json" <<'PY' 2>&1 | tee "$HIVE_OUTPUT_DIR/aisbench.log"
import json, sys
with open(sys.argv[1]) as source:
    argv = json.load(source)["argv"]
if argv[0] != "ais_bench":
    raise ValueError("Expected native AISBench argv")
sys.argv = argv
from ais_bench.benchmark.cli.main import main
sys.exit(main())
PY
    ;;
  verify-prepare)
    load_runtime client
    copy_environment_report client
    case_parameters
    python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" locate-results \
      --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
      --manifest "$(dirname -- "$HIVE_OUTPUT_DIR")/job1/manifest.json" --log "$(dirname -- "$HIVE_OUTPUT_DIR")/job1/aisbench.log" \
      --output-dir "$HIVE_OUTPUT_DIR"
    ;;
  verify)
    load_runtime client
    copy_environment_report client
    case_parameters
    exec python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" verify \
      --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
      --result-json "$HIVE_OUTPUT_DIR/result.json" --result-csv "$HIVE_OUTPUT_DIR/result.csv" \
      --output-file "$HIVE_OUTPUT_DIR/verification.json"
    ;;
  *)
    echo "Usage: task.sh verify-env server|client | serve-prepare | serve | ready | bench-prepare | bench | verify-prepare | verify" >&2
    exit 2
    ;;
esac
