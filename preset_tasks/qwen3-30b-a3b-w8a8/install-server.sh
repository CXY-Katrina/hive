#!/usr/bin/env bash
set -euo pipefail
TASK_SERVER_DEPS="${TASK_SERVER_DEPS:-/opt/hive-env/server}"
source "$(dirname -- "${BASH_SOURCE[0]}")/image-runtime.sh"
load_server_runtime() { load_image_runtime server; }
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then return; fi
load_server_runtime
if test "${1:-install}" = verify; then
  python3 -c 'import vllm, vllm_ascend, torch, torch_npu, yaml'
  vllm --help >/dev/null
  cat "$TASK_SERVER_DEPS/environment-report.json"
  exit
fi
test "${1:-install}" = install
mkdir -p "$TASK_SERVER_DEPS"
# Reuse the image's vLLM/NPU runtime; this does not install the requested PR.
python3 - "$TASK_SERVER_DEPS/environment-report.json" <<'PY'
import importlib, json, sys
from pathlib import Path
actual = {}
for name in ("vllm", "vllm_ascend", "torch", "torch_npu", "yaml"):
    module = importlib.import_module(name)
    actual[name] = {"version": getattr(module, "__version__", None), "path": module.__file__}
report = {"runtime_mode": "image-reuse", "actual": actual}
Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + "\n")
PY
vllm --help > "$TASK_SERVER_DEPS/cli-help.log"
