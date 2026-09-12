"""Agentless admission probes, shared by onboarding and resource allocation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import posixpath
import re
import shlex
import uuid

from .domain import DomainError, decode, encode, now
from .hardware import PREAMBLE, required, sections


NETWORK_FILESYSTEMS = {'nfs', 'nfs4', 'cifs', 'ceph', 'glusterfs', 'fuse.glusterfs', 'lustre', 'gpfs', 'beegfs'}


def _path(value):
    if not isinstance(value, str) or not value.startswith('/') or '\x00' in value or '..' in value.split('/'):
        raise ValueError('Invalid mount path')
    return posixpath.normpath(value)


def _redact_source(source):
    # Never persist user:password@host from SMB/URL mount sources.
    return re.sub(r'(?<=//)[^/@]+@', '[redacted]@', source)


def parse_mounts(output, extra_paths=()):
    document = json.loads(output)
    roots = document.get('filesystems')
    if not isinstance(roots, list):
        raise ValueError('Missing findmnt filesystems')
    paths = ['/mnt'] + [_path(path) for path in extra_paths]
    result = []

    def visit(rows):
        for row in rows:
            target = _path(row['target'])
            if any(target == path or target.startswith(path.rstrip('/') + '/') for path in paths):
                fstype, source = str(row.get('fstype', '')), str(row.get('source', ''))
                options = str(row.get('options', '')).split(',')
                candidate = fstype in NETWORK_FILESYSTEMS
                clean_source = _redact_source(source)
                candidate_id = 'share-' + hashlib.sha256((fstype + '\n' + clean_source).encode()).hexdigest()[:24]
                result.append({'path': target, 'source': clean_source, 'fstype': fstype,
                               'options': [item for item in options if item in ('ro', 'rw', 'noexec', 'nosuid', 'nodev')],
                               'read_only': 'ro' in options, 'readable': False, 'writable': False,
                               'total_bytes': None, 'free_bytes': None,
                               'status': 'candidate' if candidate else 'local',
                               'candidate_id': candidate_id if candidate else None,
                               'shared_storage_id': None, 'checked_at': str(now())})
            children = row.get('children', [])
            if children:
                visit(children)

    visit(roots)
    if len(result) > 256:
        raise ValueError('Too many mount candidates')
    if len({mount['path'] for mount in result}) != len(result):
        raise ValueError('Ambiguous duplicate mount targets')
    return result


def _share_groups(mounts_by_node, connections):
    """Source identities and matching paths suggest probes, never prove sharing."""
    groups = []
    for ident, mounts in mounts_by_node.items():
        for mount in mounts:
            if not mount.get('candidate_id') or not mount.get('readable') or not mount.get('writable'):
                continue
            keys = {('source', mount['candidate_id']), ('path', mount['path'])}
            members = [(connections[ident], mount)]
            # Merge connected candidates, including hostname/IP aliases. Multiple
            # paths from a single participant remain ambiguous and fail closed.
            remaining = []
            for group_keys, group_members in groups:
                if keys & group_keys:
                    keys |= group_keys
                    members.extend(group_members)
                else:
                    remaining.append((group_keys, group_members))
            # Newly merged keys may connect an earlier group too.
            while any(keys & other_keys for other_keys, _ in remaining):
                next_remaining = []
                for other_keys, other_members in remaining:
                    if keys & other_keys:
                        keys |= other_keys
                        members.extend(other_members)
                    else:
                        next_remaining.append((other_keys, other_members))
                remaining = next_remaining
            groups = remaining + [(keys, members)]
    return [members for _, members in groups]


def _matching_share_mounts(node, mounts, storage_id):
    """Resolve a verified group to fresh mount evidence without trusting the path alone."""
    previous = decode(node.get('mounts'), []) or []
    identities = {(mount.get('path'), mount.get('candidate_id')) for mount in previous
                  if mount.get('status') == 'verified' and mount.get('shared_storage_id') == storage_id
                  and mount.get('candidate_id')}
    return [mount for mount in mounts
            if mount.get('candidate_id') and
            ((mount['path'], mount['candidate_id']) in identities or mount['candidate_id'] == storage_id)]


def _retain_share_history(node, mounts):
    # A selection rechecks one share. Do not erase another share's historical
    # group mapping; retain its original timestamp, never claim a fresh check.
    previous = {(mount.get('path'), mount.get('candidate_id')): mount
                for mount in (decode(node.get('mounts'), []) or [])
                if mount.get('status') == 'verified' and mount.get('shared_storage_id')}
    for mount in mounts:
        old = previous.get((mount['path'], mount.get('candidate_id')))
        if old and mount.get('readable') and mount.get('writable'):
            for key in ('status', 'shared_storage_id', 'checked_at', 'cleanup', 'detail'):
                if key in old:
                    mount[key] = old[key]


class AdmissionProbe:
    def __init__(self, db, transport, adapters, inventory):
        self.db, self.transport, self.adapters, self.inventory = db, transport, adapters, inventory

    def _adapter(self, node):
        name = node.get('adapter', 'ascend')
        adapter = self.adapters.get(name)
        if adapter is None:
            raise DomainError('No hardware adapter for node')
        return adapter

    def _record_mounts(self, node, mounts):
        self.inventory.record_probe(node['id'], mounts=mounts)

    def discover_mounts(self, node):
        result = self.transport.run(node, PREAMBLE + 'findmnt --json --output TARGET,SOURCE,FSTYPE,OPTIONS\n', timeout=15)
        if result.code:
            raise DomainError('Mount discovery failed or timed out', 503)
        metadata = decode(node.get('metadata'), {}) or {}
        try:
            mounts = parse_mounts(result.stdout, metadata.get('mount_paths', []))
        except (ValueError, KeyError, TypeError):
            raise DomainError('Unrecognized mount inventory; shared storage is unverified', 503) from None
        script = PREAMBLE
        for index, mount in enumerate(mounts):
            path = shlex.quote(mount['path'])
            script += f'emit readable_{index} test -r {path}\nemit writable_{index} test -w {path}\n'
            script += f'emit capacity_{index} df --output=size,avail -B1 -- {path}\n'
        script += "printf 'HIVE_END\\n'\n"
        if mounts:
            result = self.transport.run(node, script, timeout=20)
            try:
                if result.code:
                    raise ValueError('Mount access collection failed')
                details = sections(result.stdout)
                for index, mount in enumerate(mounts):
                    mount['readable'] = details.get(f'readable_{index}', (1, ''))[0] == 0
                    mount['writable'] = not mount['read_only'] and details.get(f'writable_{index}', (1, ''))[0] == 0
                    capacity = details.get(f'capacity_{index}', (1, ''))
                    if capacity[0] == 0:
                        values = re.findall(r'^\s*(\d+)\s+(\d+)\s*$', capacity[1], re.M)
                        if len(values) == 1:
                            mount['total_bytes'], mount['free_bytes'] = map(int, values[0])
            except ValueError:
                # Discovery is still useful, but no mount may satisfy a task's access constraint.
                for mount in mounts:
                    mount['detail'] = 'Mount access verification incomplete'
                    mount['readable'] = mount['writable'] = False
        return mounts

    def _host_check(self, source, target):
        host, port = target['host'], int(target.get('port', 22))
        if not 1 <= port <= 65535 or not re.fullmatch(r'[A-Za-z0-9._:\-]+', host):
            return {'status': 'unknown', 'detail': 'Invalid management endpoint'}
        # Bash /dev/tcp probes the peer from the node; no node-to-node SSH credentials needed.
        script = 'set -e\nexec 3<> ' + shlex.quote(f'/dev/tcp/{host}/{port}') + '\nexec 3>&-\n'
        try:
            result = self.transport.run(source, script, timeout=5)
            return {'status': 'passed' if result.code == 0 else 'failed',
                    'detail': 'Management SSH TCP port reachable' if result.code == 0 else 'Management SSH TCP probe failed',
                    'port': port}
        except DomainError:
            return {'status': 'unknown', 'detail': 'Management TCP probe unavailable', 'port': port}

    def _pair(self, source, target, source_device, target_device, host_result):
        try:
            result = self._adapter(source).connectivity(source, target, source_device, target_device)
        except (DomainError, ValueError):
            result = {'status': 'unknown', 'detail': 'NPU probe unavailable'}
        return self._record_pair(source_device, target_device, host_result, result)

    def _record_pair(self, source_device, target_device, host_result, result, cursor=None):
        status = result.get('status', 'unknown')
        if host_result['status'] != 'passed':
            status = host_result['status']
        detail = {'management': host_result, 'npu': result}
        def record(db_cursor):
            db_cursor.execute('INSERT INTO connectivity_checks (source_device,target_device,status,detail,checked_at) '
                           'VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE '
                           'status=VALUES(status),detail=VALUES(detail),checked_at=VALUES(checked_at)',
                           (source_device['id'], target_device['id'], status, encode(detail), now()))
        if cursor is None:
            with self.db.transaction() as db_cursor:
                record(db_cursor)
        else:
            record(cursor)
        return {'source_device': source_device['id'], 'target_device': target_device['id'],
                'status': status, 'detail': detail}

    def _network(self, selected, connections):
        adapter_names = {connections[item['node_id']].get('adapter', 'ascend') for item in selected}
        adapter = self.adapters.get(next(iter(adapter_names))) if len(adapter_names) == 1 else None
        if adapter is not None and callable(getattr(adapter, 'connectivity_many', None)):
            return self._network_many(selected, connections, adapter)
        results, host_checks = [], {}
        for source_device in selected:
            for target_device in selected:
                source_id, target_id = source_device['node_id'], target_device['node_id']
                if source_id == target_id:
                    continue
                pair = (source_id, target_id)
                if pair not in host_checks:
                    host_checks[pair] = self._host_check(connections[source_id], connections[target_id])
                results.append(self._pair(connections[source_id], connections[target_id],
                                          source_device, target_device, host_checks[pair]))
        return results

    def _network_many(self, selected, connections, adapter):
        node_ids = sorted({item['node_id'] for item in selected})
        nodes = [dict(connections[ident], devices=[item for item in selected if item['node_id'] == ident])
                 for ident in node_ids]
        host_pairs = [(source, target) for source in node_ids for target in node_ids if source != target]
        if not host_pairs:
            return []
        def check(pair):
            source, target = pair
            return pair, self._host_check(connections[source], connections[target])
        with ThreadPoolExecutor(max_workers=min(8, len(host_pairs))) as pool:
            host_checks = dict(pool.map(check, host_pairs))
        try:
            batch = adapter.connectivity_many(nodes)
            evidence, duplicates = {}, set()
            for result in batch:
                key = (result['source_device'], result['target_device'])
                if key in evidence:
                    duplicates.add(key)
                evidence[key] = result
            for key in duplicates:
                evidence.pop(key, None)
        except (DomainError, ValueError, KeyError, TypeError):
            evidence = {}
        results = []
        with self.db.transaction() as cursor:
            for source in selected:
                for target in selected:
                    if source['node_id'] == target['node_id']:
                        continue
                    result = evidence.get((source['id'], target['id']))
                    if not result or result.get('source_node') != source['node_id'] or result.get('target_node') != target['node_id']:
                        result = {'status': 'unknown', 'detail': 'NPU batch probe evidence missing or ambiguous'}
                    results.append(self._record_pair(source, target,
                                   host_checks[(source['node_id'], target['node_id'])], result, cursor))
        return results

    def _verify_share(self, members, storage_id):
        """Every participant writes; all others verify. Clean only successfully created files."""
        probe_id = uuid.uuid4().hex
        created, cleanup_failures = [], []
        tokens = {node['id']: uuid.uuid4().hex for node, _ in members}
        success = False
        try:
            if len(members) < 2 or len({node['id'] for node, _ in members}) != len(members):
                raise DomainError('Cross-node sharing requires two distinct participants')
            for node, mount in members:
                if not mount.get('writable') or not mount.get('readable'):
                    raise DomainError('Shared mount is not confirmed readable and writable')
                parent = posixpath.join(_path(mount['path']), '.hive-probe')
                folder = posixpath.join(parent, probe_id)
                file = posixpath.join(folder, tokens[node['id']])
                # Refuse symlink probe directories and never remove any pre-existing file.
                script = ('set -e\numask 077\n'
                          f'test ! -L {shlex.quote(parent)}\nmkdir -p -- {shlex.quote(parent)}\n'
                          f'test ! -L {shlex.quote(folder)}\nmkdir -p -- {shlex.quote(folder)}\n'
                          f'(set -o noclobber; printf %s {shlex.quote(tokens[node["id"]])} > {shlex.quote(file)})\n')
                created.append((node, file, folder, tokens[node['id']]))
                result = self.transport.run(node, script, timeout=10)
                if result.code:
                    raise DomainError('Shared storage probe write failed')
            for node, mount in members:
                for other, _ in members:
                    file = posixpath.join(mount['path'], '.hive-probe', probe_id, tokens[other['id']])
                    script = (f'test ! -L {shlex.quote(file)} && '
                              f'test "$(cat -- {shlex.quote(file)})" = {shlex.quote(tokens[other["id"]])}\n')
                    result = self.transport.run(node, script, timeout=10)
                    if result.code:
                        raise DomainError('Shared storage cross-node read mismatch')
            success = True
        except (DomainError, ValueError):
            success = False
        finally:
            for node, file, folder, token in created:
                # Removing our exact random file, followed by rmdir only if empty; never recursive.
                script = (f'if test -e {shlex.quote(file)}; then\n'
                          f'test ! -L {shlex.quote(file)} && test "$(cat -- {shlex.quote(file)})" = {shlex.quote(token)} '
                          f'&& rm -- {shlex.quote(file)} || exit 1\nfi\n'
                          f'rmdir -- {shlex.quote(folder)} 2>/dev/null || true\n')
                try:
                    if self.transport.run(node, script, timeout=10).code:
                        cleanup_failures.append(node['id'])
                except DomainError:
                    cleanup_failures.append(node['id'])
        for _, mount in members:
            mount['status'] = 'verified' if success else 'candidate'
            mount['shared_storage_id'] = storage_id if success else None
            mount['checked_at'] = str(now())
            mount['cleanup'] = 'incomplete' if cleanup_failures else 'complete'
            mount['detail'] = 'Cross-node read/write verified' if success else 'Cross-node read/write not verified'
        return success

    def run(self, node_id):
        node = self.inventory.connection(node_id)
        peers = [peer for peer in self.inventory.list_nodes()
                 if peer['id'] != node_id and peer.get('cluster_name') == node.get('cluster_name')]
        connections = {node_id: node}
        mounts_by_node, errors = {}, []
        for brief in [node] + peers:
            try:
                connection = connections.setdefault(brief['id'], self.inventory.connection(brief['id']))
                mounts_by_node[brief['id']] = self.discover_mounts(connection)
            except DomainError:
                # Invalidate old mount verification when a fresh probe could not complete.
                mounts_by_node[brief['id']] = []
                errors.append({'node_id': brief['id'], 'detail': 'Node mount probe unavailable'})
        network = []
        for peer in peers:
            if peer['id'] not in connections:
                continue
            # Admission is a bounded node-level smoke check. Allocation still
            # verifies every directed pair of the actually requested devices.
            selected = [dict(device, node_id=node_id) for device in node.get('devices', [])[:1]]
            selected += [dict(device, node_id=peer['id']) for device in connections[peer['id']].get('devices', [])[:1]]
            network.extend(self._network(selected, connections))
        for members in _share_groups(mounts_by_node, connections):
            # Multiple aliases of a share on one host need explicit admin selection, not guessing.
            if len(members) > 1 and len({member[0]['id'] for member in members}) == len(members):
                sources = sorted({mount['candidate_id'] for _, mount in members})
                # Preserve legacy IDs when the actual mount sources agree. Alias
                # groups get their own ID; each mount keeps its source candidate_id.
                storage_id = sources[0] if len(sources) == 1 else 'shared-' + hashlib.sha256(
                    encode([node.get('cluster_name'), sources,
                            sorted({mount['path'] for _, mount in members})]).encode()).hexdigest()[:24]
                self._verify_share(members, storage_id)
        for ident, mounts in mounts_by_node.items():
            self.inventory.record_probe(ident, mounts=mounts)
        summary = {'checked_at': str(now()), 'peer_count': len(peers),
                   'checks': len(network), 'passed': sum(item['status'] == 'passed' for item in network),
                   'errors': errors, 'scope': 'representative',
                   'detail': 'No peer nodes' if not peers else 'One NPU per node checked; selected allocation devices require full recheck'}
        self.inventory.record_probe(node_id, metadata={'admission': summary}, clear_requested=True)
        return {'summary': summary, 'connectivity': network, 'mounts': mounts_by_node.get(node_id, [])}

    def check_selection(self, devices, require_interconnect=False, shared_storage_id=None):
        if not devices:
            raise DomainError('Empty device selection')
        node_ids = {device['node_id'] for device in devices}
        connections = {ident: self.inventory.connection(ident) for ident in node_ids}
        if require_interconnect and len(node_ids) > 1:
            results = self._network(devices, connections)
            if not results or any(item['status'] != 'passed' for item in results):
                raise DomainError('Selected nodes/cards have not passed every directed interconnect probe')
        if shared_storage_id:
            members, mounts_by_node = [], {}
            for ident, node in connections.items():
                mounts = self.discover_mounts(node)
                _retain_share_history(node, mounts)
                mounts_by_node[ident] = mounts
                matching = _matching_share_mounts(node, mounts, shared_storage_id)
                if len(matching) != 1:
                    self.inventory.record_probe(ident, mounts=mounts)
                    raise DomainError('Required shared storage is absent or ambiguous on a selected node')
                members.append((node, matching[0]))
            if len(members) == 1:
                # A one-node task can use a share, but one node alone cannot prove sharing.
                selected_node = members[0][0]
                for brief in self.inventory.list_nodes():
                    if brief['id'] in connections or brief.get('cluster_name') != selected_node.get('cluster_name'):
                        continue
                    try:
                        peer = self.inventory.connection(brief['id'])
                        peer_mounts = self.discover_mounts(peer)
                        _retain_share_history(peer, peer_mounts)
                    except DomainError:
                        continue
                    matching = [mount for mount in _matching_share_mounts(peer, peer_mounts, shared_storage_id)
                                if mount['readable'] and mount['writable']]
                    if len(matching) == 1:
                        members.append((peer, matching[0]))
                        mounts_by_node[peer['id']] = peer_mounts
                        break
            passed = self._verify_share(members, shared_storage_id)
            for ident, mounts in mounts_by_node.items():
                self.inventory.record_probe(ident, mounts=mounts)
            if not passed:
                raise DomainError('Required shared storage read/write verification failed')
        return True
