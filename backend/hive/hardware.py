"""Ascend text adapter; unsupported output stays unknown, never becomes idle.

Reference formats: Huawei npu-smi `info`, `info -m` command reference;
docs/research/node-connectivity-and-containers.md for hccn_tool probes.
Actual A2/A3/A5 driver fixtures must be validated during hardware acceptance.
"""
import base64
import ipaddress
import re
import shlex

from .domain import DeviceSample, DomainError, Process, Snapshot, decode, now


PREAMBLE = r'''export LC_ALL=C
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/Ascend/driver/tools
set -o pipefail
emit() {
  local key="$1"; shift
  local output rc
  output=$("$@" 2>/dev/null); rc=$?
  printf 'HIVE\t%s\t%s\t' "$key" "$rc"
  printf '%s' "$output" | base64 -w0
  printf '\n'
}
'''

COLLECT_SCRIPT = PREAMBLE + r'''
emit boot cat /proc/sys/kernel/random/boot_id
emit mapping timeout 8 npu-smi info -m
emit info timeout 8 npu-smi info
emit driver cat /usr/local/Ascend/driver/version.info
emit firmware cat /usr/local/Ascend/firmware/version.info
emit boot_after cat /proc/sys/kernel/random/boot_id
printf 'HIVE_END\n'
'''


def sections(output):
    if not output.endswith('HIVE_END\n'):
        raise ValueError('Incomplete collection envelope')
    result = {}
    for line in output.splitlines()[:-1]:
        marker, key, code, body = line.split('\t', 3)
        if marker != 'HIVE' or key in result:
            raise ValueError('Invalid collection envelope')
        result[key] = (int(code), base64.b64decode(body, validate=True).decode('utf-8', 'strict'))
    return result


def required(data, name):
    code, value = data[name]
    if code != 0:
        raise ValueError(f'{name} command failed')
    return value


def parse_mapping(text):
    if not re.search(r'NPU\s+ID\s+Chip\s+ID\s+Chip\s+Logic\s+ID', text):
        raise ValueError('Unsupported device mapping header')
    found, logical_ids = {}, set()
    for line in text.splitlines():
        if not line.strip() or 'NPU' in line and 'Chip' in line:
            continue
        match = re.fullmatch(r'\s*(-?\d+)\s+(\d+)\s+(\d+|-)\s+(.+?)\s*', line)
        if not match:
            raise ValueError('Unsupported device mapping row')
        npu, chip, logical, name = match.groups()
        if logical == '-' and name.lower() in ('mcu', 'management controller'):
            continue
        if npu.startswith('-') or logical == '-' or (npu, chip) in found or logical in logical_ids:
            raise ValueError('Unavailable or ambiguous device mapping')
        found[(npu, chip)] = logical
        logical_ids.add(logical)
    if not found:
        raise ValueError('No valid compute device mapping')
    return found


def parse_info(text, mapping):
    """Parse standard two-line device rows and the explicitly terminated PID table."""
    if 'HBM-Usage(MB)' not in text or 'AICore(%)' not in text:
        raise ValueError('Unsupported metric table (HBM/AICore columns required)')
    marker = re.search(r'^\s*\|\s*NPU\s+Chip\s*\|\s*Process id\s*\|', text, re.M | re.I)
    if not marker:
        raise ValueError('Missing process table')
    metric_text, process_text = text[:marker.start()], text[marker.start():]
    samples, current_npu, health = {}, None, 'unknown'
    for line in metric_text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if not line.lstrip().startswith('|') or len(cells) < 3:
            continue
        head = re.match(r'^(\d+)\s+\S+', cells[0])
        if head:
            current_npu = head.group(1)
            health = cells[1].lower()
            continue
        if current_npu is None or not re.fullmatch(r'\d+', cells[0]):
            continue
        key = (current_npu, cells[0])
        if key not in mapping:
            raise ValueError('Metric table and device mapping disagree')
        # PCI bus address distinguishes metric row from hugepage/auxiliary rows.
        if not re.fullmatch(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.\d', cells[1]):
            continue
        value = ' '.join(cells[2:])
        match = re.fullmatch(r'(\d+(?:\.\d+)?)\s+(\d+)\s*/\s*(\d+)\s+(\d+)\s*/\s*(\d+)', value)
        if not match:
            raise ValueError('Missing or unrecognized AI Core/HBM values')
        ai, _, _, used, total = match.groups()
        ai, used, total = float(ai), int(used) * 1024**2, int(total) * 1024**2
        if not 0 <= ai <= 100 or not 0 <= used <= total or total <= 0 or key in samples:
            raise ValueError('Invalid metric values or duplicate device')
        samples[key] = DeviceSample(f'{key[0]}:{key[1]}', key[0], key[1], mapping[key],
                                    total, used, ai, 'OK' if health == 'ok' else 'FAULT' if health in ('fault', 'alarm', 'critical') else 'unknown')
    if set(samples) != set(mapping):
        raise ValueError('Missing devices in metric table')
    lines = [line.strip() for line in process_text.splitlines() if line.strip()]
    if not lines or not re.fullmatch(r'\+[+\-=]+\+', lines[-1]):
        raise ValueError('Unterminated process table')
    processes, empty, empty_npus, occupied_npus = {}, False, set(), set()
    for line in lines[1:]:
        if re.fullmatch(r'\+[+\-=]+\+', line):
            continue
        if re.fullmatch(r'\|\s*No running processes found\s*\|', line, re.I):
            empty = True
            continue
        empty_match = re.fullmatch(r'\|\s*No running processes found in NPU\s+(\d+)\s*\|', line, re.I)
        if empty_match:
            if empty_match.group(1) in empty_npus:
                raise ValueError('Duplicate empty NPU process row')
            empty_npus.add(empty_match.group(1))
            continue
        match = re.fullmatch(r'\|\s*(\d+)\s+(\d+)\s*\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|', line)
        if not match:
            raise ValueError('Unknown or truncated process row')
        npu, chip, pid, name, _ = match.groups()
        occupied_npus.add(npu)
        if (npu, chip) not in mapping or int(pid) <= 1:
            raise ValueError('Invalid process device or PID')
        entry = processes.setdefault(int(pid), {'name': name.strip(), 'keys': []})
        if (npu, chip) not in entry['keys']:
            entry['keys'].append((npu, chip))
    expected_npus = {key[0] for key in mapping}
    if (empty and (processes or empty_npus)) or empty_npus & occupied_npus:
        raise ValueError('Ambiguous process table')
    if not empty and (empty_npus | occupied_npus) != expected_npus:
        raise ValueError('Missing NPU process rows')
    return samples, processes


def process_script(pids):
    if len(pids) > 4096 or any(not isinstance(pid, int) or pid <= 1 for pid in pids):
        raise ValueError('Invalid process set')
    # PID values came from a validated integer parser; all other remote data is encoded.
    return PREAMBLE + r'''
emit boot cat /proc/sys/kernel/random/boot_id
docker_state=absent
if command -v docker >/dev/null 2>&1; then
  docker_state=unknown
  docker_rows=$(timeout 5 docker ps --no-trunc --format '{{.ID}}\t{{.Names}}' 2>/dev/null)
  if [ "$?" -eq 0 ]; then
    docker_state=ok
    emit docker printf '%s' "$docker_rows"
    while IFS=$'\t' read -r cid cname; do
      [[ "$cid" =~ ^[0-9a-f]{64}$ ]] || continue
      emit "top_$cid" timeout 3 docker top "$cid" -eo pid
    done <<< "$docker_rows"
  fi
fi
emit docker_state printf '%s' "$docker_state"
''' + '\nfor pid in ' + ' '.join(str(pid) for pid in sorted(pids)) + r'''; do
  emit "stat_$pid" cat "/proc/$pid/stat"
  emit "cgroup_$pid" cat "/proc/$pid/cgroup"
  emit "pidns_$pid" readlink "/proc/$pid/ns/pid"
  emit "stat_after_$pid" cat "/proc/$pid/stat"
done
emit host_pidns readlink /proc/1/ns/pid
emit info_after timeout 8 npu-smi info
emit boot_after cat /proc/sys/kernel/random/boot_id
printf 'HIVE_END\n'
'''


def process_start_time(stat, expected_pid):
    if not stat.startswith(str(expected_pid) + ' (') or ')' not in stat:
        raise ValueError('Invalid process identity')
    fields = stat.rsplit(')', 1)[1].split()
    # /proc/PID/stat starttime is field 22; state is field 3.
    if len(fields) < 20 or not fields[19].isdigit():
        raise ValueError('Missing process start time')
    return fields[19]


class AscendAdapter:
    name = 'ascend'

    def __init__(self, transport):
        self.transport = transport

    def collect(self, node):
        boot, samples = '', {}
        try:
            response = self.transport.run(node, COLLECT_SCRIPT, timeout=25)
            if response.code:
                raise ValueError('NPU collection failed or timed out')
            data = sections(response.stdout)
            boot = required(data, 'boot').strip()
            if not re.fullmatch(r'[0-9a-fA-F-]{36}', boot):
                raise ValueError('Invalid node boot identity')
            if required(data, 'boot_after') != boot:
                raise ValueError('Node rebooted during sampling')
            mapping = parse_mapping(required(data, 'mapping'))
            samples, pids = parse_info(required(data, 'info'), mapping)
            expected = {device['slot'] for device in node.get('devices', [])}
            if expected - {sample.slot for sample in samples.values()}:
                raise ValueError('Previously registered devices missing from enumeration')
            if pids:
                result = self.transport.run(node, process_script(pids), timeout=25)
                if result.code:
                    raise ValueError('Process identity collection failed')
                identities = sections(result.stdout)
                if required(identities, 'boot') != boot or required(identities, 'boot_after') != boot:
                    raise ValueError('Node rebooted during sampling')
                _, confirmed_pids = parse_info(required(identities, 'info_after'), mapping)
                if ({pid: item['keys'] for pid, item in pids.items()}
                        != {pid: item['keys'] for pid, item in confirmed_pids.items()}):
                    raise ValueError('NPU process/device membership changed during identity collection')
                for pid, item in pids.items():
                    start = process_start_time(required(identities, f'stat_{pid}'), pid)
                    if start != process_start_time(required(identities, f'stat_after_{pid}'), pid):
                        raise ValueError('PID identity changed during sampling')
                    container_id, container_name, status = self._container(pid, identities)
                    process = Process(pid, start, boot, [f'{a}:{b}' for a, b in item['keys']], item['name'],
                                      container_id, container_name, status)
                    for key in item['keys']:
                        samples[key].processes.append(process)
            for sample in samples.values():
                sample.process_complete = True
            metadata = {}
            if data.get('driver', (1, ''))[0] == 0:
                metadata['driver_version'] = self._driver_version(data['driver'][1])
            if data.get('firmware', (1, ''))[0] == 0:
                metadata['firmware_version'] = self._driver_version(data['firmware'][1])
            info_version = re.search(r'npu-smi\s+([\w.\-]+)', data['info'][1])
            if info_version:
                metadata['npu_smi_version'] = info_version.group(1)
            return Snapshot(list(samples.values()), boot, now(), metadata=metadata)
        except (ValueError, KeyError, UnicodeError, DomainError) as exc:
            for sample in samples.values():
                sample.quality = 'unknown'
                sample.reason = str(exc)
                sample.process_complete = False
            return Snapshot(list(samples.values()), boot, now(), quality='unknown', reason=str(exc))

    @staticmethod
    def _driver_version(text):
        values = dict(re.findall(r'^\s*([A-Za-z_]+)\s*=\s*([^\r\n]+)', text, re.M))
        return values.get('DriverVersion') or values.get('Version') or values.get('version') or 'unknown'

    @staticmethod
    def _container(pid, data):
        code, cgroup = data.get(f'cgroup_{pid}', (1, ''))
        ids = set(re.findall(r'(?:/docker/|docker-)([0-9a-f]{64})(?:\.scope|/|$)', cgroup, re.M)) if code == 0 else set()
        state = data.get('docker_state', (1, 'unknown'))[1]
        names = {}
        for line in data.get('docker', (1, ''))[1].splitlines():
            parts = line.split('\t', 1)
            if len(parts) == 2 and re.fullmatch(r'[0-9a-f]{64}', parts[0]):
                names[parts[0]] = parts[1]
        if not ids and state == 'ok':
            for cid in names:
                top_code, top = data.get(f'top_{cid}', (1, ''))
                if top_code == 0 and str(pid) in [line.strip() for line in top.splitlines()[1:]]:
                    ids.add(cid)
        if len(ids) == 1:
            cid = next(iter(ids))
            return cid, names.get(cid), 'docker' if cid in names else 'unknown'
        # Merely failing to find Docker is not evidence of a host process.
        host_ns = data.get('host_pidns', (1, ''))
        pid_ns = data.get(f'pidns_{pid}', (1, ''))
        if not ids and state == 'absent' and code == 0 and host_ns[0] == pid_ns[0] == 0 and host_ns[1] == pid_ns[1]:
            paths = [line.split(':', 2)[-1] for line in cgroup.splitlines()]
            if paths and all(re.fullmatch(r'/(?:|(?:system|user)\.slice(?:/[^/]+)*)', path) for path in paths):
                return None, None, 'host'
        return None, None, 'unknown'

    def environment(self, devices):
        ids = [str(device['logical_id']) for device in devices]
        if not ids or len(ids) != len(set(ids)) or any(not re.fullmatch(r'\d+', value) for value in ids):
            raise DomainError('Invalid Ascend logical device mapping')
        return {'ASCEND_RT_VISIBLE_DEVICES': ','.join(ids)}

    @staticmethod
    def _network_id(node, device):
        extensions = decode(device.get('extensions'), {}) or {}
        metadata = decode(node.get('metadata'), {}) or {}
        explicit = extensions.get('hccn_id', metadata.get('hccn_ids', {}).get(device['slot']))
        # A3's dual-chip numbering is not inferred from a contradictory card mapping.
        value = explicit if explicit is not None else device['command_id'] if node['generation'] == 'A2' else None
        if value is None or not re.fullmatch(r'\d+', str(value)):
            raise ValueError('Explicit hccn_id mapping required for this device')
        return str(value)

    def connectivity(self, source, target, source_device, target_device):
        generation = source['generation']
        if generation not in ('A2', 'A3') or target['generation'] != generation:
            return {'status': 'unknown', 'detail': 'Unsupported generation pair; no verified probe command'}
        try:
            source_id = self._network_id(source, source_device)
            target_id = self._network_id(target, target_device)
            mode = 'ip' if generation == 'A2' else 'vnic'
            script = PREAMBLE + f'emit address hccn_tool -i {target_id} -{mode} -g\nemit tls hccn_tool -i {target_id} -tls -g\nprintf "HIVE_END\\n"\n'
            response = self.transport.run(target, script, timeout=15)
            if response.code:
                raise ValueError('Peer address/TLS query failed')
            peer = sections(response.stdout)
            # `-ip -g` also prints a netmask; it must never be mistaken for a peer address.
            addresses = re.findall(r'\b(?:vnic[_ ]?)?(?:ipaddr|ip_address|ip address|ip)\s*[:=]\s*((?:\d{1,3}\.){3}\d{1,3})',
                                   required(peer, 'address'), re.I)
            addresses = list(dict.fromkeys(str(ipaddress.ip_address(value)) for value in addresses))
            if len(addresses) != 1:
                raise ValueError('Peer NPU address unavailable or ambiguous')
            peer_tls = self._tls(required(peer, 'tls'))
            ping_mode = 'ping' if generation == 'A2' else 'hccs_ping'
            script = PREAMBLE + f'emit tls hccn_tool -i {source_id} -tls -g\nemit ping hccn_tool -i {source_id} -{ping_mode} -g address {shlex.quote(addresses[0])}\nprintf "HIVE_END\\n"\n'
            response = self.transport.run(source, script, timeout=15)
            if response.code:
                raise ValueError('Source NPU probe failed')
            result = sections(response.stdout)
            tls = self._tls(required(result, 'tls'))
            if tls != peer_tls:
                return {'status': 'failed', 'detail': 'NPU TLS switch mismatch'}
            ping = required(result, 'ping')
            success = 'This pkt ping success' in ping and not re.search(r'\b(?:fail(?:ed|ure)?|error)\b', ping, re.I)
            return {'status': 'passed' if success else 'failed', 'detail': 'NPU probe completed',
                    'peer_address': addresses[0], 'tls_switch': tls}
        except (ValueError, KeyError, DomainError) as exc:
            return {'status': 'unknown', 'detail': str(exc)}

    @staticmethod
    def _tls(text):
        # Official HDK output: dev_id:0, tls switch[0](0:disable, 1:enable), ...
        matches = re.findall(r'\bswitch\s*\[\s*([01])\s*\]', text, re.I)
        matches += re.findall(r'\bswitch\s*[:=]\s*([A-Za-z0-9]+)', text, re.I)
        if len(matches) != 1 or matches[0].lower() not in ('0', '1', 'on', 'off', 'enable', 'disable', 'enabled', 'disabled'):
            raise ValueError('Unrecognized NPU TLS switch')
        return 'on' if matches[0].lower() in ('1', 'on', 'enable', 'enabled') else 'off'
