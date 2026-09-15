#!/usr/bin/env bash
set -euo pipefail
TASK_CLIENT_DEPS=/opt/hive-env/client
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
load_image_runtime client
# The base image must already contain the NPU stack; do not resolve a replacement.
python3 -c 'import torch, torch_npu, numpy'
TASK_AISBENCH_SHA="${TASK_AISBENCH_SHA:-0da56eadb2ac85c31c2540f4f5b69af3ec5717a5}"
benchmark_source="$(hive_resource package aisbench-source)"
wheelhouse="$(hive_resource package python-wheelhouse 2>/dev/null || true)"
if test -n "$wheelhouse"; then
  test -d "$wheelhouse"
  export PIP_FIND_LINKS="file://$wheelhouse" PIP_NO_INDEX=1
fi
mkdir -p "$TASK_CLIENT_DEPS"
# Private checkout and venv: never modify shared AISBench or the image's packages.
git clone --no-checkout --no-hardlinks "$benchmark_source" "$TASK_CLIENT_DEPS/benchmark"
git -C "$TASK_CLIENT_DEPS/benchmark" checkout --detach "$TASK_AISBENCH_SHA"
test "$(git -C "$TASK_CLIENT_DEPS/benchmark" rev-parse HEAD)" = "$TASK_AISBENCH_SHA"
python3 -m venv --system-site-packages "$TASK_CLIENT_DEPS/venv"
# Reuse installed large packages. These constraints avoid replacing Torch/NPU.
python3 - > "$TASK_CLIENT_DEPS/constraints.txt" <<'PY'
from importlib.metadata import PackageNotFoundError, version
for name in ("torch", "torch-npu", "torchvision", "torchaudio", "numpy", "triton-ascend", "scipy", "numba", "transformers"):
    try:
        print(f"{name}=={version(name)}")
    except PackageNotFoundError:
        pass
PY
# This frozen AISBench needs NumPy 1.x compatible OpenCV and Pillow in its venv.
"$TASK_CLIENT_DEPS/venv/bin/python3" -m pip install --upgrade-strategy only-if-needed \
  -c "$TASK_CLIENT_DEPS/constraints.txt" -e "$TASK_CLIENT_DEPS/benchmark[api]" \
  PyYAML opencv-python-headless==4.11.0.86 Pillow==11.2.1
export PATH="$TASK_CLIENT_DEPS/venv/bin:$PATH"
export PYTHONPATH="$TASK_CLIENT_DEPS/benchmark${PYTHONPATH:+:$PYTHONPATH}"
python3 -c 'from ais_bench.benchmark.cli.main import main; main()' --help > "$TASK_CLIENT_DEPS/cli-help.log"
# Keep existing-image dependency diagnostics visible without claiming a clean solve.
python3 -m pip check > "$TASK_CLIENT_DEPS/pip-check.log" 2>&1 || true
python3 - "$TASK_CLIENT_DEPS" <<'PY'
import importlib.metadata as metadata, json, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
report = {"runtime_mode": "image-reuse-client-venv", "python": sys.executable,
          "aisbench_sha": subprocess.check_output(["git", "-C", str(root / "benchmark"), "rev-parse", "HEAD"], text=True).strip(),
          "versions": {name: metadata.version(name) for name in ("numpy", "opencv-python-headless", "Pillow", "PyYAML")},
          "pip_check": (root / "pip-check.log").read_text()}
(root / "environment-report.json").write_text(json.dumps(report, indent=2) + "\n")
PY
