#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
load_image_runtime server
root=/opt/hive-env/server
mkdir -p "$root"
# Follow script.md: install matching vLLM, then the selected vLLM-Ascend checkout.
vllm_sha="$(python3 - "$HIVE_SOURCE_DIR/.github/vllm-main-verified.commit" <<'PY'
from pathlib import Path
import re,sys
value=Path(sys.argv[1]).read_text(encoding="utf-8").strip()
if not re.fullmatch(r'[0-9a-fA-F]{40}',value):
    raise ValueError('Expected a full vLLM commit SHA in .github/vllm-main-verified.commit')
print(value.lower())
PY
)"
git clone --no-checkout https://github.com/vllm-project/vllm.git "$root/vllm"
git -C "$root/vllm" checkout --detach "$vllm_sha"
test "$(git -C "$root/vllm" rev-parse HEAD)" = "$vllm_sha"
cd "$root/vllm"
python3 -m pip install setuptools-rust
bash ./build_rust.sh
VLLM_TARGET_DEVICE=empty python3 -m pip install -v -e . --no-build-isolation --no-index --no-deps
cd "$HIVE_SOURCE_DIR"
# Run from the checkout root. Do not silently patch upstream setup.py.
python3 -m pip install -v -e . --no-build-isolation --no-index --no-deps
python3 - "$root/environment-report.json" "$root/vllm" "$HIVE_SOURCE_DIR" <<'PY'
import importlib,json,subprocess,sys
from pathlib import Path
actual={}
for name in ('vllm','vllm_ascend','torch','torch_npu','yaml'):
    module=importlib.import_module(name)
    actual[name]={'version':getattr(module,'__version__',None),'path':module.__file__}
for name,root in (('vllm',sys.argv[2]),('vllm_ascend',sys.argv[3])):
    if not Path(actual[name]['path']).resolve().is_relative_to(Path(root).resolve()):
        raise RuntimeError(f'{name} did not load from the installed checkout')
report={'runtime_mode':'source-editable','actual':actual,'source':{}}
for name,root in (('vllm',sys.argv[2]),('vllm_ascend',sys.argv[3])):
    report['source'][name]={'path':root,'commit':subprocess.check_output(['git','-C',root,'rev-parse','HEAD'],text=True).strip(),
      'diff':subprocess.check_output(['git','-C',root,'diff','--stat'],text=True)}
Path(sys.argv[1]).write_text(json.dumps(report,indent=2)+'\n')
PY
vllm --help > "$root/cli-help.log"
