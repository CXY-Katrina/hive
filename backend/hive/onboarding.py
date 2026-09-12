"""Verify SSH and read basic identity before persisting an admitted node."""
import os
import re
import tempfile
import threading

import paramiko

from .domain import DomainError, now
from .hardware import PREAMBLE, sections, required
from .host_system import host_system
from .hardware_versions import hdk_info
from .ssh import SSHTransport, HostKeyRequired, host_key_info


_host_keys_lock = threading.Lock()
BASIC_SCRIPT = PREAMBLE + '''emit boot cat /proc/sys/kernel/random/boot_id
emit uname uname -a
emit machine uname -m
emit system bash -c 'cat /sys/class/dmi/id/product_name 2>/dev/null || dmidecode -s system-product-name'
emit driver cat /usr/local/Ascend/driver/version.info
emit npu_smi bash -c 'command -v npu-smi'
printf 'HIVE_END\\n'
'''


class Onboarding:
    def __init__(self, settings):
        self.settings = settings

    def check(self, payload, enroll=False):
        transport = SSHTransport(self.settings)
        try:
            with transport._connection(payload) as client:
                key = client.get_transport().get_remote_server_key()
            result = transport.run(payload, BASIC_SCRIPT, timeout=15)
            if result.code:
                raise DomainError('SSH 已连接，但基本信息检测失败或超时，请检查 Bash 和系统命令', 422)
            try:
                data = sections(result.stdout)
                boot = required(data, 'boot').strip()
                if not re.fullmatch(r'[0-9a-fA-F-]{36}', boot):
                    raise ValueError('Invalid boot identity')
                required(data, 'uname')
                required(data, 'machine')
            except (ValueError, KeyError):
                raise DomainError('SSH 已连接，但无法完整读取系统基本信息', 422) from None
            if data.get('npu_smi', (1, ''))[0] != 0:
                raise DomainError('SSH 已连接，但未找到 npu-smi，请检查 Ascend 驱动安装和命令路径', 422)
            model = data.get('system', (1, ''))[1].strip() if data.get('system', (1, ''))[0] == 0 else ''
            if model.lower() in {'unknown', 'none', 'not specified', 'to be filled by o.e.m.', 'default string'}:
                model = ''
            stamp = str(now())
            profile = {'system_product': model[:128] or None, 'board_product': None,
                       'soc_versions': [], 'devices': [], 'quality': 'unknown',
                       'reason': '基本信息已读取，等待 NPU 与 SoC 检测', 'checked_at': stamp,
                       'source': 'DMI product_name; dmidecode -s system-product-name',
                       'boot_id': boot, 'host_system': host_system(data, stamp)}
            if enroll:
                self._enroll(payload, key)
            return {'status': 'ok', 'model': model[:128], 'boot_id': boot,
                    'host_key': host_key_info(key), 'checked_at': stamp,
                    'metadata': {'hardware_profile': profile, 'hdk': hdk_info(data.get('driver', (1, '')), stamp)},
                    'detail': 'SSH 连接正常，基本信息已读取' if model else 'SSH 连接正常；系统未提供机型，后续检测会继续尝试'}
        except HostKeyRequired as exc:
            if enroll:
                raise
            return {'status': 'host_key_required', 'host_key': exc.host_key, 'detail': str(exc)}
        finally:
            transport.close()

    def _enroll(self, payload, key):
        path = self.settings.known_hosts
        host = payload['host'] if int(payload.get('port', 22)) == 22 else f"[{payload['host']}]:{payload['port']}"
        with _host_keys_lock:
            keys = paramiko.HostKeys()
            if path.exists():
                keys.load(str(path))
            existing = keys.lookup(host)
            if existing and key.get_name() in existing:
                if existing[key.get_name()] != key:
                    raise DomainError('SSH 主机公钥已变化，请核验服务器身份', 409)
                return
            keys.add(host, key.get_name(), key)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.known-hosts-', dir=path.parent)
            os.close(fd)
            try:
                keys.save(temporary)
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
