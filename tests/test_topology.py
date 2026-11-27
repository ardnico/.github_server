import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_topology import build, create_private, render


class TopologyTests(unittest.TestCase):
    def setUp(self):
        self.inventory = {'schema_version': 1, 'hostname': 'lab-01',
                          'observed_at': '2026-11-22T13:00:00+00:00',
                          'environment': {'kind': 'host_or_vm'},
                          'collection_status': 'complete',
                          'running_service_names': {'status': 'complete', 'values': ['ssh.service']}}
        self.content = json.dumps(self.inventory).encode()
        self.sha = hashlib.sha256(self.content).hexdigest()

    def test_inventory_does_not_invent_dependencies(self):
        topology = build(self.content, Path('host.json'))
        self.assertEqual(topology['edges'][0]['relationship'], 'runs-on')
        self.assertEqual(len(topology['edges']), 1)
        self.assertIn('未作成', render(topology))
        self.assertEqual(topology['inventory_sha256'], self.sha)

    def test_reviewed_claim_can_be_drawn_with_provenance(self):
        ev = {'kind': 'user-confirmed', 'source_ref': 'user:reviewed',
              'observed_at': '2026-11-22T13:05:00+00:00'}
        node_ev = {'kind': 'hypothesis', 'source_ref': 'user:node-note',
                   'observed_at': '2026-11-22T13:07:00+00:00'}
        claims = {'schema_version': 1, 'hostname': 'lab-01', 'inventory_sha256': self.sha,
                  'nodes': [{'id': 'db-01', 'type': 'host', 'label': 'db-01', 'evidence': node_ev}],
                  'edges': [{'from': 'service:ssh.service', 'to': 'db-01',
                             'relationship': 'connects-to', 'evidence': ev}]}
        topology = build(self.content, Path('host.json'), claims)
        self.assertEqual(len(topology['edges']), 2)
        markdown = render(topology)
        self.assertIn('user-confirmed: user:reviewed | 2026-11-22T13:05:00+00:00', markdown)
        self.assertIn('| `db-01` | db-01 | host | hypothesis: user:node-note | '
                      '2026-11-22T13:07:00+00:00 |', markdown)
        claims['inventory_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'do not match'):
            build(self.content, Path('host.json'), claims)
        with self.assertRaisesRegex(ValueError, 'do not match'):
            build(self.content, Path('host.json'), {})

    def test_untrusted_or_dangling_claims_are_rejected(self):
        ev = {'kind': 'hypothesis', 'source_ref': 'user:guess',
              'observed_at': '2026-11-22T13:05:00+00:00'}
        base = {'schema_version': 1, 'hostname': 'lab-01', 'inventory_sha256': self.sha,
                'nodes': [], 'edges': [{'from': 'service:ssh.service', 'to': 'other',
                                        'relationship': 'depends-on', 'evidence': ev}]}
        with self.assertRaisesRegex(ValueError, 'dangling'):
            build(self.content, Path('host.json'), base)
        base['nodes'] = [{'id': 'other', 'type': 'host', 'label': 'bad"] --> attack', 'evidence': ev}]
        with self.assertRaisesRegex(ValueError, 'label'):
            build(self.content, Path('host.json'), base)
        self.inventory['environment']['kind'] = 'container'
        with self.assertRaisesRegex(ValueError, 'local host'):
            build(json.dumps(self.inventory).encode(), Path('host.json'))

    def test_private_output_is_exclusive(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / 'private' / 'topology.json'
            create_private(path, '{}')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(FileExistsError):
                create_private(path, '{}')
            Path(dirname).chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'private'):
                create_private(Path(dirname) / 'other.json', '{}')


if __name__ == '__main__':
    unittest.main()
