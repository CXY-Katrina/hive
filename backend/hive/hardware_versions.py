"""Version evidence from the installed HDK driver package, not CANN or ToolBox."""
import re


def hdk_info(result, checked_at):
    code, text = result
    info = {'version': None, 'quality': 'unknown', 'checked_at': checked_at,
            'source': 'cat /usr/local/Ascend/driver/version.info',
            'field': None, 'reason': 'HDK 驱动版本文件不可读或缺少有效版本字段'}
    if code:
        return info
    for key in ('package_version', 'Version', 'DriverVersion', 'version'):
        values = set(re.findall(r'^\s*' + key + r'\s*=\s*([^\r\n]*)', text, re.M))
        if not values:
            continue
        values = {value.strip() for value in values}
        if len(values) != 1:
            info['reason'] = 'HDK 版本字段存在冲突'
            return info
        value = values.pop()
        if not re.fullmatch(r'[0-9][A-Za-z0-9._+\-]{0,95}', value):
            return info
        info.update(version=value, field=key, quality='ok', reason=None)
        return info
    return info
