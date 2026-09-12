"""Shared tool discovery for optional Ascend-DMI queries and benchmarks."""
from .hardware import PREAMBLE


DMI_SETUP = r'''
export PATH=/usr/local/Ascend/toolbox/latest/Ascend-DMI/bin:/usr/local/Ascend/toolbox/latest/bin:$PATH
HIVE_ASCEND_DMI=$(command -v ascend-dmi || true)
if [ -z "$HIVE_ASCEND_DMI" ]; then
  for hive_candidate in /usr/local/Ascend/toolbox/*/Ascend-DMI/bin/ascend-dmi; do
    if [ -x "$hive_candidate" ]; then HIVE_ASCEND_DMI="$hive_candidate"; break; fi
  done
fi
if [ -n "$HIVE_ASCEND_DMI" ]; then
  hive_dmi_dir=$(dirname "$(readlink -f "$HIVE_ASCEND_DMI")")
  export LD_LIBRARY_PATH="$hive_dmi_dir/../lib64:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common:${LD_LIBRARY_PATH:-}"
fi
'''
DMI_PREAMBLE = PREAMBLE + DMI_SETUP


def tool_profile(data, stamp):
    path_code, path = data.get('dmi_path', (1, ''))
    code, version = data.get('dmi_version', (1, ''))
    path = path.strip() if path_code == 0 else ''
    available = bool(path) and code == 0 and bool(version.strip())
    return {'available': available, 'path': path or None,
            'version': version.strip()[:512] if available else None,
            'reason': None if available else 'ascend-dmi 不可用，请检查运行库依赖' if path else '未安装 ascend-dmi 或不在受支持的工具路径',
            'checked_at': stamp, 'source': 'command -v ascend-dmi; ascend-dmi --version'}
