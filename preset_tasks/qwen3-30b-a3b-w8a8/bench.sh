#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
load_image_runtime client
TASK_CLIENT_DEPS=/opt/hive-env/client
export PATH="$TASK_CLIENT_DEPS/venv/bin:$PATH"
export PYTHONPATH="$TASK_CLIENT_DEPS/benchmark${PYTHONPATH:+:$PYTHONPATH}"
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
