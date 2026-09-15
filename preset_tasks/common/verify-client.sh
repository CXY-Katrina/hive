#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
load_image_runtime client
TASK_CLIENT_DEPS="${1:?client dependency directory}"
export PATH="$TASK_CLIENT_DEPS/venv/bin:$PATH"
export PYTHONPATH="$TASK_CLIENT_DEPS/benchmark${PYTHONPATH:+:$PYTHONPATH}"
python3 -c 'from ais_bench.benchmark.cli.main import main; main()' --help >/dev/null
cat $TASK_CLIENT_DEPS/environment-report.json
