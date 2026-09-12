"""Explicit, whole-node Ascend measurements; no unattended benchmark retries."""
from datetime import datetime, timedelta
import math
import re

from .domain import DomainError, SYSTEM, decode, encode, now, uid
from .inventory import device_status


ACTIVE = {'QUEUED', 'RUNNING', 'RECOVERING'}
DEVICE_TIMEOUT = 60


def parse_fp16(output, logical_id):
    """Accept the documented normal table only, never guess a number from logs."""
    expected = str(logical_id)
    if not re.fullmatch(r'0|[1-9][0-9]{0,3}', expected):
        raise DomainError('无效的 Ascend Device ID', 422)
    header = False
    values = []
    for line in output.splitlines():
        if all(label in line for label in ('Device', 'Execute Times', 'Duration(ms)', 'TFLOPS@FP16', 'Power(W)')):
            positions = [line.index(label) for label in ('Device', 'Execute Times', 'Duration(ms)', 'TFLOPS@FP16', 'Power(W)')]
            header = positions == sorted(positions)
            continue
        if not header:
            continue
        fields = line.strip().strip('|').split('|') if '|' in line else line.split()
        fields = [field.strip() for field in fields]
        if len(fields) != 5 or not re.fullmatch(r'[0-9]+', fields[0]):
            continue
        if fields[0] != expected:
            raise DomainError('Ascend-DMI 返回的 Device ID 与被测设备不符')
        if not re.fullmatch(r'(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)', fields[1]):
            raise DomainError('Ascend-DMI 执行次数格式无效')
        try:
            numbers = [float(fields[1].replace(',', ''))] + [float(value) for value in fields[2:]]
        except ValueError:
            raise DomainError('Ascend-DMI 算力表包含无效数值') from None
        if not all(math.isfinite(value) and value > 0 for value in numbers[:3]):
            raise DomainError('Ascend-DMI 未返回有效的 FP16 实测结果')
        values.append(numbers[2])
    if len(values) != 1:
        raise DomainError('Ascend-DMI FP16 结果缺失、重复或输出格式不支持')
    return values[0]


def benchmark_script(logical_id):
    from .ascend_dmi import DMI_PREAMBLE
    if not isinstance(logical_id, str) or not re.fullmatch(r'0|[1-9][0-9]{0,3}', logical_id):
        raise DomainError('无效的 Ascend Device ID', 422)
    # Do not source arbitrary shell profiles or install runtime dependencies.
    # -q acknowledges the tool's reminder only for this explicit admin action;
    # whole-node process and ownership checks are performed independently above.
    return DMI_PREAMBLE + '\n' + r'''
test -n "${HIVE_ASCEND_DMI:-}" && test -x "$HIVE_ASCEND_DMI" || exit 127
for hive_lib in /usr/local/Ascend/ascend-toolkit/latest/lib64 /usr/local/Ascend/ascend-toolkit/latest/runtime/lib64 /usr/local/Ascend/cann/lib64 /usr/local/Ascend/cann/latest/lib64; do
  if test -d "$hive_lib"; then export LD_LIBRARY_PATH="$hive_lib:${LD_LIBRARY_PATH:-}"; fi
done
''' + f'exec timeout --signal=TERM --kill-after=2 {DEVICE_TIMEOUT}s "$HIVE_ASCEND_DMI" -f -t fp16 -d {logical_id} --et 10 --fmt normal -q </dev/null\n'


class ComputeBenchmark:
    def __init__(self, db, settings, inventory, telemetry, transport):
        self.db, self.settings, self.inventory = db, settings, inventory
        self.telemetry, self.transport = telemetry, transport

    @staticmethod
    def state(node):
        return (decode(node.get('metadata'), {}) or {}).get('compute_benchmark') or {}

    def _locked(self, cursor, node_id):
        # Same first lock as ResourceService.reserve and Telemetry.ingest.
        cursor.execute('SELECT * FROM nodes WHERE id=%s AND deleted_at IS NULL FOR UPDATE', (node_id,))
        node = cursor.fetchone()
        if not node:
            raise DomainError('节点不存在', 404)
        return node

    def _idle(self, cursor, node, expected=None):
        stamp = now()
        if node['adapter'] != 'ascend' or node['vendor'] != 'ascend':
            raise DomainError('仅支持 Ascend 整机手动实测', 422)
        if (node['status'] != 'ok' or not node['sampled_at']
                or (stamp - node['sampled_at']).total_seconds() > self.settings.stale_seconds):
            raise DomainError('需要整机完整且新鲜的正常采样')
        cursor.execute('''SELECT d.*,o.request_id,n.adapter FROM devices d JOIN nodes n ON n.id=d.node_id
                          LEFT JOIN device_ownership o ON o.device_id=d.id
                          WHERE d.node_id=%s ORDER BY d.slot''', (node['id'],))
        devices = list(cursor.fetchall())
        if not devices or any(not d['baseline_confirmed'] or d['boot_id'] != node['boot_id']
                              or device_status(d, stamp, self.settings.stale_seconds) != 'available' for d in devices):
            raise DomainError('整机必须无申请、无进程、AI Core 为 0，且所有卡显存处于已确认空闲基线内')
        identifiers = [d['logical_id'] for d in devices]
        if len(set(identifiers)) != len(identifiers) or any(not re.fullmatch(r'0|[1-9][0-9]{0,3}', value or '') for value in identifiers):
            raise DomainError('逻辑设备 ID 清单不完整或无效')
        fingerprint = [(d['id'], d['slot'], d['logical_id'], d['command_id'], d['chip_id']) for d in devices]
        if expected is not None and (node['boot_id'] != expected['boot_id'] or fingerprint != [tuple(d) for d in expected['mapping']]):
            raise DomainError('测试前节点重启或设备映射已改变')
        return devices, fingerprint

    def _save(self, cursor, node_id, state, maintenance=True):
        cursor.execute("UPDATE nodes SET maintenance=%s,metadata=JSON_SET(COALESCE(metadata,JSON_OBJECT()),'$.compute_benchmark',CAST(%s AS JSON)) WHERE id=%s",
                       (maintenance, encode(state), node_id))

    def request(self, node_id, actor):
        self.inventory.require_admin(actor)
        with self.db.transaction() as c:
            node = self._locked(c, node_id)
            if self.state(node).get('status') in ACTIVE:
                raise DomainError('该节点已有算力测试，等待完成或安全恢复')
            devices, mapping = self._idle(c, node)
            profile = (decode(node.get('metadata'), {}) or {}).get('hardware_profile') or {}
            if profile.get('boot_id') != node['boot_id'] or not (profile.get('ascend_dmi') or {}).get('available'):
                raise DomainError('节点未检测到可用 Ascend-DMI，请先安装工具并重新检查', 422)
            state = dict(id=uid(), status='QUEUED', requested_at=str(now()), requested_by=actor.username,
                         precision='FP16', unit='TFLOPS', scope='per_logical_device', execute_times=10,
                         timeout_seconds_per_device=DEVICE_TIMEOUT,
                         command='ascend-dmi -f -t fp16 -d <Device ID> --et 10 --fmt normal -q',
                         devices=[], boot_id=node['boot_id'], mapping=mapping,
                         previous_maintenance=bool(node['maintenance']))
            self._save(c, node_id, state)
            self.db.audit(c, actor, 'node.compute_benchmark.request', node_id, {'benchmark_id': state['id']})
            return state

    def pending(self):
        rows = self.db.all("SELECT id,metadata FROM nodes WHERE JSON_UNQUOTE(JSON_EXTRACT(metadata,'$.compute_benchmark.status')) IN ('QUEUED','RUNNING','RECOVERING') ORDER BY host")
        stamp = now()
        return [row['id'] for row in rows if self.state(row).get('status') != 'RECOVERING'
                or self._due(self.state(row), stamp)]

    @staticmethod
    def _due(state, stamp):
        value = state.get('recover_after')
        return not value or datetime.fromisoformat(value) <= stamp

    def _recover(self, node_id, benchmark_id):
        # The command might outlive the worker/SSH connection. Never retry it.
        try:
            self.telemetry.collect_node(node_id)
            with self.db.transaction() as c:
                node = self._locked(c, node_id)
                state = self.state(node)
                if state.get('id') != benchmark_id or not self._due(state, now()):
                    return
                _, mapping = self._idle(c, node)
                succeeded = bool(state.pop('all_devices_succeeded', False))
                if node['boot_id'] != state['boot_id'] or mapping != [tuple(d) for d in state['mapping']]:
                    succeeded = False
                    state['reason'] = '测试期间节点重启或设备映射改变，实测结果无效'
                state.update(status='SUCCEEDED' if succeeded else 'FAILED', finished_at=str(now()))
                state.pop('recovery_reason', None)
                if succeeded:
                    values = [row['tflops'] for row in state['devices']]
                    state.update(min_tflops=min(values), max_tflops=max(values), reason=None)
                self._save(c, node_id, state, state['previous_maintenance'])
                self.db.audit(c, SYSTEM, 'node.compute_benchmark.finish', node_id,
                              {'benchmark_id': benchmark_id, 'status': state['status'], 'devices': state['devices']})
        except Exception as exc:
            with self.db.transaction() as c:
                node = self._locked(c, node_id)
                state = self.state(node)
                if state.get('id') == benchmark_id:
                    state.update(status='RECOVERING', recovery_reason=str(exc) if isinstance(exc, DomainError) else type(exc).__name__,
                                 recover_after=str(now() + timedelta(seconds=15)))
                    self._save(c, node_id, state)

    def run(self, node_id):
        with self.db.transaction() as c:
            node = self._locked(c, node_id)
            state = self.state(node)
            if state.get('status') not in ACTIVE:
                return
            if state['status'] != 'QUEUED':
                if state['status'] == 'RUNNING':
                    state.update(status='RECOVERING', reason='执行被中断；等待远端超时并复核整机空闲，不自动重试')
                    self._save(c, node_id, state)
                recovering = True
            else:
                state.update(status='RUNNING', started_at=str(now()), recover_after=str(now()))
                self._save(c, node_id, state)
                recovering = False
        if recovering:
            if self._due(state, now()):
                self._recover(node_id, state['id'])
            return
        try:
            for original in state['mapping']:
                self.telemetry.collect_node(node_id)
                connection = self.inventory.connection(node_id)
                with self.db.transaction() as c:
                    node = self._locked(c, node_id)
                    current = self.state(node)
                    if current.get('id') != state['id'] or current.get('status') != 'RUNNING':
                        raise DomainError('算力测试状态已改变')
                    devices, _ = self._idle(c, node, state)
                    device = next(d for d in devices if d['id'] == original[0])
                    # Transport capacity/connect/auth/open each have a bounded wait.
                    # Add slack before recovery to cover a worker lost during dispatch.
                    state['recover_after'] = str(now() + timedelta(seconds=DEVICE_TIMEOUT + 6 * self.settings.ssh_timeout + 60))
                    self._save(c, node_id, state)
                result = self.transport.run(connection, benchmark_script(device['logical_id']), timeout=DEVICE_TIMEOUT + 5)
                output = result.stdout + ('\nSTDERR:\n' + result.stderr if result.stderr else '')
                if connection.get('password'):
                    output = output.replace(connection['password'], '[redacted]')
                state['last_output'] = output[:4096]
                state['last_device_id'] = device['logical_id']
                if result.code != 0:
                    raise DomainError(f'Ascend-DMI 执行失败（退出码 {result.code}）；检查工具/运行库兼容性及日志，未自动重试')
                value = parse_fp16(result.stdout, device['logical_id'])
                state['devices'].append(dict(device_id=device['id'], slot=device['slot'], logical_id=device['logical_id'],
                                             tflops=value, output=output[:4096]))
                with self.db.transaction() as c:
                    self._locked(c, node_id)
                    self._save(c, node_id, state)
            state.update(all_devices_succeeded=True, recover_after=str(now()))
        except Exception as exc:
            state['reason'] = str(exc) if isinstance(exc, DomainError) else '算力测试中断: ' + type(exc).__name__
        state['status'] = 'RECOVERING'
        with self.db.transaction() as c:
            self._locked(c, node_id)
            self._save(c, node_id, state)
        if self._due(state, now()):
            self._recover(node_id, state['id'])
