#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/task.sh"
load_runtime server
mkdir -p "$TASK_SERVER_DEPS"
# Reuse the image's vLLM/NPU runtime; this does not install the requested PR.
python3 - "$HIVE_SOURCE_DIR" "$TASK_SERVER_DEPS/environment-report.json" <<'PY'
import importlib, json, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
actual = {}
for name in ("vllm", "vllm_ascend", "torch", "torch_npu", "yaml"):
    module = importlib.import_module(name)
    actual[name] = {"version": getattr(module, "__version__", None), "path": module.__file__}
report = {"runtime_mode": "image-reuse", "actual": actual,
          "requested_ascend_sha": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
          "requested_vllm_sha": (root / ".github/vllm-main-verified.commit").read_text().strip()}
Path(sys.argv[2]).write_text(json.dumps(report, indent=2) + "\n")
PY
vllm --help > "$TASK_SERVER_DEPS/cli-help.log"
