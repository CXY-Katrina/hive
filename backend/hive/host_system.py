"""Hardware-independent host identity; retain evidence without guessing from model names."""
import re


def host_system(data, stamp):
    uname_code, uname = data.get('uname', (1, ''))
    machine_code, machine = data.get('machine', (1, ''))
    uname = uname.strip() if uname_code == 0 else ''
    machine = machine.strip() if machine_code == 0 else ''
    architecture = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'x86_64', 'amd64': 'x86_64'}.get(machine.lower())
    if not architecture and re.fullmatch(r'armv[5-8][a-z0-9]*', machine.lower()):
        architecture = 'arm'
    if not architecture and re.fullmatch(r'i[3-6]86', machine.lower()):
        architecture = 'x86'
    # uname -m is the kernel's machine field; hostname/kernel version substrings
    # in uname -a must never be used to classify the host architecture.
    complete = bool(uname and architecture)
    return {'architecture': architecture or 'unknown', 'machine': machine or None,
            'uname': uname[:4096] or None, 'quality': 'ok' if complete else 'unknown',
            'reason': None if complete else 'uname 查询失败或 CPU 架构尚未识别',
            'source': 'uname -a; uname -m', 'checked_at': stamp}
