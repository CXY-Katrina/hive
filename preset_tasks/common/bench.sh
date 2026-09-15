#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
load_image_runtime client
TASK_CLIENT_DEPS="${1:?client dependency directory}"
TASK_MANIFEST="${2:?benchmark manifest path}"
TASK_LOG="${3:?benchmark log path}"
export PATH="$TASK_CLIENT_DEPS/venv/bin:$PATH"
export PYTHONPATH="$TASK_CLIENT_DEPS/benchmark${PYTHONPATH:+:$PYTHONPATH}"
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
