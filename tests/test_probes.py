"""No nodes contacted: test pair coverage and storage evidence using isolated fakes."""
from contextlib import contextmanager
from copy import deepcopy
import json
import unittest

from hive.domain import CommandResult, DomainError
from hive.probes import AdmissionProbe, parse_mounts


class DB:
    def __init__(self):
        self.calls = []

    @contextmanager
    def transaction(self):
        yield self

    def execute(self, sql, args):
        self.calls.append((sql, args))


class Inventory:
    def __init__(self, nodes):
        self.nodes = {node['id']: node for node in nodes}
        self.recorded = []

    def connection(self, ident):
        return self.nodes[ident]

    def list_nodes(self):
        return list(self.nodes.values())

    def record_probe(self, ident, mounts=None, metadata=None, clear_requested=False):
        self.recorded.append((ident, mounts, metadata, clear_requested))
        if mounts is not None:
            self.nodes[ident]['mounts'] = deepcopy(mounts)


class Adapter:
    def __init__(self, failure=None):
        self.calls, self.failure = [], failure

    def connectivity(self, source, target, source_device, target_device):
        pair = (source_device['id'], target_device['id'])
        self.calls.append(pair)
        return {'status': 'failed' if pair == self.failure else 'passed', 'detail': 'fixture'}


class BatchAdapter(Adapter):
    def __init__(self, missing=False, duplicate=False):
        super().__init__()
        self.missing, self.duplicate, self.nodes = missing, duplicate, []

    def connectivity_many(self, nodes):
        self.nodes = nodes
        results = [{'source_node': source['id'], 'target_node': target['id'],
                    'source_device': source_device['id'], 'target_device': target_device['id'],
                    'status': 'passed', 'detail': 'batch fixture'}
                   for source in nodes for target in nodes if source['id'] != target['id']
                   for source_device in source['devices'] for target_device in target['devices']]
        return results[1:] if self.missing else results + ([results[0]] if self.duplicate else [])


class Transport:
    def __init__(self, fail_read=False):
        self.calls, self.fail_read = [], fail_read

    def run(self, node, script, timeout=20):
        self.calls.append((node, script))
        if self.fail_read and script.startswith('test ! -L'):
            return CommandResult('', '', 1)
        return CommandResult('', '', 0)


def node(ident):
    return {'id': ident, 'host': '10.0.0.' + ident, 'port': 2222, 'adapter': 'ascend', 'cluster_name': 'default',
            'devices': [{'id': ident + 'a', 'node_id': ident}, {'id': ident + 'b', 'node_id': ident}]}


class ProbeTests(unittest.TestCase):
    def test_admission_is_representative_but_allocation_still_checks_every_card(self):
        nodes = [node(str(index)) for index in range(1, 4)]
        adapter, inventory = Adapter(), Inventory(nodes)
        probe = AdmissionProbe(DB(), Transport(), {'ascend': adapter}, inventory)
        probe.discover_mounts = lambda node: []
        report = probe.run('1')
        self.assertEqual(report['summary']['scope'], 'representative')
        self.assertEqual(set(adapter.calls), {('1a', '2a'), ('2a', '1a'), ('1a', '3a'), ('3a', '1a')})
        adapter.calls.clear()
        probe.check_selection([d for n in nodes for d in n['devices']], require_interconnect=True)
        self.assertEqual(len(adapter.calls), 24)

    def alias_probe(self, fail_read=False):
        nodes = [node(str(index)) for index in range(1, 6)]
        transport, inventory = Transport(fail_read), Inventory(nodes)
        probe = AdmissionProbe(DB(), transport, {}, inventory)
        discovered = {}
        for index, item in enumerate(nodes):
            sources = ('suzblue.server:/share', ':/weight') if index < 2 else (
                '178.27.1.16:/share', '178.27.1.18:/weight')
            mounts = parse_mounts(json.dumps({'filesystems': [
                {'target': path, 'source': source, 'fstype': 'nfs4', 'options': 'rw'}
                for path, source in zip(('/mnt/share', '/mnt/weight'), sources)]}))
            for mount in mounts:
                mount['readable'] = mount['writable'] = True
            discovered[item['id']] = mounts
        probe.discover_mounts = lambda item: deepcopy(discovered[item['id']])
        probe._network = lambda selected, connections: []
        return probe, transport, inventory, discovered

    def test_admission_verifies_all_five_alias_mounts_without_changing_source_ids(self):
        probe, transport, inventory, discovered = self.alias_probe()
        probe.run('1')
        for index in (0, 1):
            mounts = [item['mounts'][index] for item in inventory.nodes.values()]
            self.assertEqual(len({mount['shared_storage_id'] for mount in mounts}), 1)
            self.assertTrue(all(mount['status'] == 'verified' for mount in mounts))
            self.assertEqual(len({mount['candidate_id'] for mount in mounts}), 2)
            for ident, item in inventory.nodes.items():
                self.assertEqual(item['mounts'][index]['candidate_id'], discovered[ident][index]['candidate_id'])
        self.assertNotEqual(inventory.nodes['1']['mounts'][0]['shared_storage_id'],
                            inventory.nodes['1']['mounts'][1]['shared_storage_id'])
        # Each share: five writers, every writer read on every node, five cleanups.
        self.assertEqual(len(transport.calls), 2 * (5 + 25 + 5))

    def test_matching_paths_without_shared_contents_never_get_verified(self):
        probe, _, inventory, _ = self.alias_probe(fail_read=True)
        probe.run('1')
        self.assertTrue(all(mount['shared_storage_id'] is None
                            for item in inventory.nodes.values() for mount in item['mounts']))

    def test_selection_resolves_verified_alias_group_and_rechecks_actual_contents(self):
        probe, transport, inventory, _ = self.alias_probe()
        probe.run('1')
        storage_id = inventory.nodes['1']['mounts'][0]['shared_storage_id']
        transport.calls.clear()
        devices = [item['devices'][0] for item in inventory.nodes.values()]
        self.assertTrue(probe.check_selection(devices, shared_storage_id=storage_id))
        self.assertEqual(len(transport.calls), 35)
        transport.fail_read = True
        with self.assertRaises(DomainError):
            probe.check_selection(devices, shared_storage_id=storage_id)
        self.assertTrue(all(item['mounts'][0]['shared_storage_id'] is None
                            for item in inventory.nodes.values()))

    def test_selection_rejects_changed_source_even_when_mount_path_matches(self):
        probe, transport, inventory, discovered = self.alias_probe()
        probe.run('1')
        storage_id = inventory.nodes['1']['mounts'][0]['shared_storage_id']
        discovered['1'][0]['candidate_id'] = 'share-different-storage'
        transport.calls.clear()
        with self.assertRaises(DomainError):
            probe.check_selection(inventory.nodes['1']['devices'], shared_storage_id=storage_id)
        self.assertEqual(transport.calls, [])

    def test_single_node_alias_selection_still_requires_a_peer(self):
        probe, transport, inventory, _ = self.alias_probe()
        probe.run('1')
        storage_id = inventory.nodes['1']['mounts'][0]['shared_storage_id']
        transport.calls.clear()
        self.assertTrue(probe.check_selection(inventory.nodes['1']['devices'], shared_storage_id=storage_id))
        self.assertEqual(len(transport.calls), 8)

    def test_selection_preserves_other_share_history_for_later_reverification(self):
        probe, transport, inventory, _ = self.alias_probe()
        probe.run('1')
        shares = deepcopy(inventory.nodes['1']['mounts'])
        timestamps = {ident: item['mounts'][1]['checked_at'] for ident, item in inventory.nodes.items()}
        devices = [item['devices'][0] for item in inventory.nodes.values()]
        probe.check_selection(devices, shared_storage_id=shares[0]['shared_storage_id'])
        for ident, item in inventory.nodes.items():
            self.assertEqual(item['mounts'][1]['shared_storage_id'], shares[1]['shared_storage_id'])
            self.assertEqual(item['mounts'][1]['checked_at'], timestamps[ident])
        transport.calls.clear()
        self.assertTrue(probe.check_selection(devices, shared_storage_id=shares[1]['shared_storage_id']))
        self.assertEqual(len(transport.calls), 35)

    def test_local_mnt_is_not_shared(self):
        output = json.dumps({'filesystems': [{'target': '/mnt', 'source': '/dev/sda1', 'fstype': 'ext4', 'options': 'rw'}]})
        mount = parse_mounts(output)[0]
        self.assertEqual(mount['status'], 'local')
        self.assertIsNone(mount['candidate_id'])
        self.assertIsNone(mount['shared_storage_id'])

    def test_network_mount_is_only_a_candidate_and_options_redacted(self):
        output = json.dumps({'filesystems': [{'target': '/mnt/data', 'source': '//user:secret@server/share',
                                             'fstype': 'cifs', 'options': 'rw,password=secret,username=user'}]})
        mount = parse_mounts(output)[0]
        self.assertEqual(mount['status'], 'candidate')
        self.assertNotIn('secret', json.dumps(mount))
        self.assertFalse(mount['writable'])

    def test_nested_mounts_discovered_without_user_file_scanning(self):
        output = json.dumps({'filesystems': [{'target': '/', 'source': '/dev/sda1', 'fstype': 'ext4',
                    'children': [{'target': '/mnt/data', 'source': 'server:/data', 'fstype': 'nfs4', 'options': 'ro'}]}]})
        mounts = parse_mounts(output)
        self.assertEqual(len(mounts), 1)
        self.assertTrue(mounts[0]['read_only'])

    def test_three_nodes_all_card_pairs_and_directions(self):
        nodes = [node(str(index)) for index in range(1, 4)]
        adapter, transport, db = Adapter(), Transport(), DB()
        probe = AdmissionProbe(db, transport, {'ascend': adapter}, Inventory(nodes))
        devices = [device for item in nodes for device in item['devices']]
        self.assertTrue(probe.check_selection(devices, require_interconnect=True))
        self.assertEqual(len(adapter.calls), 24)
        self.assertEqual(len(set(adapter.calls)), 24)
        self.assertIn(('1a', '3b'), adapter.calls)
        self.assertIn(('3b', '1a'), adapter.calls)
        self.assertEqual(len(transport.calls), 6)
        self.assertTrue(all('/2222' in script and script.startswith('set -e') for _, script in transport.calls))

    def test_batch_connectivity_receives_only_selected_cards_and_records_all_directions(self):
        nodes = [node(str(index)) for index in range(1, 4)]
        adapter, transport, db = BatchAdapter(), Transport(), DB()
        probe = AdmissionProbe(db, transport, {'ascend': adapter}, Inventory(nodes))
        devices = [item['devices'][0] for item in nodes]
        self.assertTrue(probe.check_selection(devices, require_interconnect=True))
        self.assertEqual([len(item['devices']) for item in adapter.nodes], [1, 1, 1])
        self.assertEqual(len(db.calls), 6)
        self.assertEqual(len(transport.calls), 6)
        self.assertEqual(adapter.calls, [])

    def test_batch_missing_or_duplicate_evidence_rejects_selection(self):
        nodes = [node('1'), node('2')]
        for adapter in (BatchAdapter(missing=True), BatchAdapter(duplicate=True)):
            probe = AdmissionProbe(DB(), Transport(), {'ascend': adapter}, Inventory(nodes))
            with self.assertRaises(DomainError):
                probe.check_selection([item['devices'][0] for item in nodes], require_interconnect=True)

    def test_nontransitive_failure_rejects_whole_selection(self):
        nodes = [node(str(index)) for index in range(1, 4)]
        adapter = Adapter(failure=('1a', '3b'))
        probe = AdmissionProbe(DB(), Transport(), {'ascend': adapter}, Inventory(nodes))
        with self.assertRaises(DomainError):
            probe.check_selection([device for item in nodes for device in item['devices']], require_interconnect=True)

    def test_no_interconnect_option_does_not_probe(self):
        adapter = Adapter()
        nodes = [node('1'), node('2')]
        probe = AdmissionProbe(DB(), Transport(), {'ascend': adapter}, Inventory(nodes))
        self.assertTrue(probe.check_selection([device for item in nodes for device in item['devices']]))
        self.assertEqual(adapter.calls, [])

    def test_storage_verification_writes_each_node_and_cleans_exact_files(self):
        transport = Transport()
        probe = AdmissionProbe(DB(), transport, {}, Inventory([]))
        members = [(node('1'), {'path': '/mnt/data', 'readable': True, 'writable': True}),
                   (node('2'), {'path': '/mnt/models', 'readable': True, 'writable': True})]
        self.assertTrue(probe._verify_share(members, 'share-test'))
        self.assertEqual(len(transport.calls), 8)  # 2 writes + 4 reads + 2 cleanups
        for _, mount in members:
            self.assertEqual(mount['status'], 'verified')
        scripts = '\n'.join(script for _, script in transport.calls)
        self.assertNotIn('rm -r', scripts)
        self.assertNotIn('rm -f', scripts)
        self.assertIn('noclobber', scripts)

    def test_same_mount_name_with_unshared_contents_fails_and_cleans(self):
        transport = Transport(fail_read=True)
        probe = AdmissionProbe(DB(), transport, {}, Inventory([]))
        members = [(node('1'), {'path': '/mnt/data', 'readable': True, 'writable': True}),
                   (node('2'), {'path': '/mnt/data', 'readable': True, 'writable': True})]
        self.assertFalse(probe._verify_share(members, 'share-test'))
        self.assertTrue(all(mount['shared_storage_id'] is None for _, mount in members))
        self.assertEqual(sum('rm --' in script for _, script in transport.calls), 2)

    def test_readonly_candidate_is_not_marked_writable_shared(self):
        transport = Transport()
        probe = AdmissionProbe(DB(), transport, {}, Inventory([]))
        members = [(node('1'), {'path': '/mnt/data', 'readable': True, 'writable': False})]
        self.assertFalse(probe._verify_share(members, 'share-test'))
        self.assertEqual(transport.calls, [])

    def test_one_writable_node_does_not_prove_cross_node_sharing(self):
        transport = Transport()
        probe = AdmissionProbe(DB(), transport, {}, Inventory([]))
        members = [(node('1'), {'path': '/mnt/data', 'readable': True, 'writable': True})]
        self.assertFalse(probe._verify_share(members, 'share-test'))
        self.assertIsNone(members[0][1]['shared_storage_id'])
        self.assertEqual(transport.calls, [])

    def test_admission_only_clears_the_completed_target_request(self):
        nodes = [node(str(index)) for index in range(1, 4)]
        inventory = Inventory(nodes)
        probe = AdmissionProbe(DB(), Transport(), {'ascend': Adapter()}, inventory)
        probe.discover_mounts = lambda connection: []
        probe.run('1')
        self.assertEqual([ident for ident, _, _, clear in inventory.recorded if clear], ['1'])
        self.assertEqual({ident for ident, mounts, _, clear in inventory.recorded if mounts is not None and not clear},
                         {'1', '2', '3'})


if __name__ == '__main__':
    unittest.main()
