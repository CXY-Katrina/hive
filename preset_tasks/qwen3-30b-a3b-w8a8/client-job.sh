#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/install-client.sh"
source "$(dirname -- "${BASH_SOURCE[0]}")/case-common.sh"
case "${1:-}" in
  bench-prepare)
    load_client_runtime
    copy_environment_report "$TASK_CLIENT_DEPS"
    case_parameters
    python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" prepare \
      --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
      --benchmark-home "$TASK_CLIENT_DEPS/benchmark" \
      --model-path "$(hive_resource model "$TASK_MODEL_NAME")" --dataset-path "$(hive_resource dataset "$TASK_DATASET_NAME")" \
      --host "${HIVE_NODE0_IP:?}" --port "$TASK_PORT" --output-dir "$HIVE_OUTPUT_DIR"
    ;;
  bench)
    load_client_runtime
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
    load_client_runtime
    copy_environment_report "$TASK_CLIENT_DEPS"
    case_parameters
    python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" locate-results \
      --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
      --manifest "$(dirname -- "$HIVE_OUTPUT_DIR")/job1/manifest.json" --log "$(dirname -- "$HIVE_OUTPUT_DIR")/job1/aisbench.log" \
      --output-dir "$HIVE_OUTPUT_DIR"
    ;;
  verify)
    load_client_runtime
    copy_environment_report "$TASK_CLIENT_DEPS"
    case_parameters
    exec python3 "$TASK_SCRIPTS_DIR/nightly_cli.py" verify \
      --config "$TASK_CASE_YAML" --case "$TASK_CASE" --benchmark "$TASK_BENCHMARK" \
      --result-json "$HIVE_OUTPUT_DIR/result.json" --result-csv "$HIVE_OUTPUT_DIR/result.csv" \
      --output-file "$HIVE_OUTPUT_DIR/verification.json"
    ;;
  *) echo "Expected bench-prepare | bench | verify-prepare | verify" >&2; exit 2 ;;
esac
