"""Infrequent hardware identity collection, separate from time-series metrics."""
import re

from .domain import DomainError, now
from .hardware import PREAMBLE, required, sections
from .ascend_dmi import DMI_PREAMBLE, tool_profile
from .host_system import host_system


def fields(text):
    result = {}
    for line in text.splitlines():
        key, separator, value = line.strip().partition(':')
        if separator:
            key, value = key.strip(), value.strip()
            if key in result and result[key] != value:
                raise ValueError('Conflicting hardware identity fields')
            result[key] = value
    return result


def soc_version(text):
    info = fields(text)
    name, kind, suffix = (info.get(key, '') for key in ('Chip Name', 'Chip Type', 'NPU Name'))
    kind = '' if kind.upper() in ('NA', 'N/A') else kind
    suffix = '' if suffix.upper() in ('NA', 'N/A') else suffix
    if kind.lower() == 'ascend' and re.fullmatch(r'(?:310|910)[A-Za-z0-9]+', name) and not suffix:
        return 'Ascend' + name
    if re.fullmatch(r'Ascend(?:910|950)', name, re.I) and not kind and re.fullmatch(r'[0-9]{4}', suffix):
        return 'Ascend' + name[6:] + '_' + suffix
    if re.fullmatch(r'Ascend(?:310|910)[A-Za-z][A-Za-z0-9]*', name, re.I) and not kind and not suffix:
        return 'Ascend' + name[6:]
    raise ValueError('Detailed SoC version unavailable; generic chip names are insufficient')


class AscendHardwareProfile:
    def __init__(self, transport):
        self.transport = transport

    def collect(self, node):
        profile = {'system_product': None, 'board_product': None, 'soc_versions': [], 'devices': [],
                   'quality': 'unknown', 'reason': None, 'checked_at': str(now()), 'boot_id': node.get('boot_id'),
                   'source': 'DMI product_name; npu-smi info -t board -i <NPU ID> [-c <Chip ID>]'}
        profile['host_system'] = host_system({}, profile['checked_at'])
        # A failed SSH query is not evidence that the tool is uninstalled.
        profile['ascend_dmi'] = None
        devices = node.get('devices', [])
        if not devices or len(devices) > 128:
            profile['reason'] = 'Complete device inventory required'
            return profile
        try:
            for device in devices:
                if not all(re.fullmatch(r'[0-9]+', str(device[key])) for key in ('command_id', 'chip_id')):
                    raise ValueError('Invalid hardware query device identity')
            first = devices[0]['command_id']
            script = DMI_PREAMBLE + ('emit boot cat /proc/sys/kernel/random/boot_id\n'
                      'emit uname uname -a\nemit machine uname -m\n'
                      'emit dmi_path printf "%s" "$HIVE_ASCEND_DMI"\n'
                      'emit dmi_version timeout 5 "$HIVE_ASCEND_DMI" --version\n'
                      'emit system cat /sys/class/dmi/id/product_name\n'
                      f'emit board timeout 3 npu-smi info -t board -i {first}\nprintf "HIVE_END\\n"\n')
            result = self.transport.run(node, script, timeout=15)
            if result.code:
                raise ValueError('Hardware product query incomplete')
            product = sections(result.stdout)
            if required(product, 'boot').strip() != node.get('boot_id'):
                raise ValueError('Node rebooted before hardware identity query')
            profile['host_system'] = host_system(product, profile['checked_at'])
            profile['ascend_dmi'] = tool_profile(product, profile['checked_at'])
            if product.get('system', (1, ''))[0] == 0:
                profile['system_product'] = product['system'][1].strip()[:128] or None
            if product.get('board', (1, ''))[0] == 0:
                profile['board_product'] = fields(product['board'][1]).get('Product Name')
            for offset in range(0, len(devices), 4):
                batch = devices[offset:offset + 4]
                script = PREAMBLE + 'emit boot cat /proc/sys/kernel/random/boot_id\n'
                for index, device in enumerate(batch):
                    script += f"emit chip_{index} timeout 3 npu-smi info -t board -i {device['command_id']} -c {device['chip_id']}\n"
                script += 'emit boot_after cat /proc/sys/kernel/random/boot_id\nprintf "HIVE_END\\n"\n'
                result = self.transport.run(node, script, timeout=18)
                if result.code:
                    raise ValueError('SoC query timed out or incomplete')
                data = sections(result.stdout)
                if required(data, 'boot').strip() != node.get('boot_id') or required(data, 'boot_after').strip() != node.get('boot_id'):
                    raise ValueError('Node rebooted during hardware identity query')
                for index, device in enumerate(batch):
                    row = {'slot': device['slot'], 'soc_version': None, 'chip_version': None}
                    try:
                        raw = required(data, f'chip_{index}')
                        identity = fields(raw)
                        if identity.get('NPU ID') != str(device['command_id']) or identity.get('Chip ID') != str(device['chip_id']):
                            raise ValueError('SoC response identity does not match requested device')
                        row['soc_version'] = soc_version(raw)
                        row['chip_version'] = identity.get('Chip Version')
                    except (ValueError, KeyError) as exc:
                        row['reason'] = str(exc)
                    profile['devices'].append(row)
            profile['soc_versions'] = sorted({d['soc_version'] for d in profile['devices'] if d['soc_version']})
            profile['quality'] = 'ok' if all(d['soc_version'] for d in profile['devices']) else 'partial'
        except (DomainError, ValueError, KeyError) as exc:
            profile['quality'] = 'partial' if profile['devices'] else 'unknown'
            profile['reason'] = str(exc)
            profile['soc_versions'] = sorted({d['soc_version'] for d in profile['devices'] if d['soc_version']})
        return profile
