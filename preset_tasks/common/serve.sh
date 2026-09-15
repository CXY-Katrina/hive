#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
load_image_runtime server
TASK_COMMAND="${1:?generated server command JSON}"
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
