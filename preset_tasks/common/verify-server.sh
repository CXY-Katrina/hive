#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
load_image_runtime server
python3 -c 'import vllm,vllm_ascend,torch,torch_npu,yaml'
vllm --help >/dev/null
cat "${1:?server dependency directory}/environment-report.json"
