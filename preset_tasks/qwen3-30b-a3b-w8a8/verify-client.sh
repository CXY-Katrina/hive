#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
load_image_runtime client
export PATH="/opt/hive-env/client/venv/bin:$PATH"
export PYTHONPATH="/opt/hive-env/client/benchmark${PYTHONPATH:+:$PYTHONPATH}"
python3 -c 'from ais_bench.benchmark.cli.main import main; main()' --help >/dev/null
cat /opt/hive-env/client/environment-report.json
