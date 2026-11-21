"""Verify that a reviewed plan cannot silently change its host or operation."""

from datetime import datetime, timezone, timedelta
import json
import unittest

from tools.run_plan import digest, validate


class PlanValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.inventory = {'schema_version': 1, 'hostname': 'lab-host', 'observed_at': self.now.isoformat()}
        self.inventory_bytes = json.dumps(self.inventory).encode()
        self.plan = {'schema_version': 1, 'plan_id': 'lab-01', 'hostname': 'lab-host', 'inventory_sha256': digest(self.inventory_bytes),
                     'operations': [{'kind': 'service-status', 'target': 'sshd.service'}]}

    def check(self, plan=None, apply=False):
        plan = plan or self.plan
        raw = json.dumps(plan).encode()
        return validate(plan, self.inventory, raw, self.inventory_bytes, digest(raw), apply,
                        now=self.now, hostname='lab-host')

    def test_read_only_command(self):
        self.assertEqual(self.check(), [['systemctl', 'status', '--no-pager', '--lines=0', 'sshd.service']])

    def test_rejects_shell_metacharacters(self):
        self.plan['operations'][0]['target'] = 'sshd;reboot'
        with self.assertRaises(ValueError):
            self.check()

    def test_requires_apply_for_upgrade(self):
        self.plan['operations'] = [{'kind': 'compose-upgrade', 'target': 'app'}]
        with self.assertRaisesRegex(ValueError, 'requires --apply'):
            self.check()
        self.assertEqual(self.check(apply=True), [['svc-update', 'upgrade', 'app', '--apply']])

    def test_rejects_wrong_host_and_stale_inventory(self):
        self.plan['hostname'] = 'another-host'
        with self.assertRaisesRegex(ValueError, 'host mismatch'):
            self.check()
        self.plan['hostname'] = 'lab-host'
        self.inventory['observed_at'] = (self.now - timedelta(days=2)).isoformat()
        self.inventory_bytes = json.dumps(self.inventory).encode()
        self.plan['inventory_sha256'] = digest(self.inventory_bytes)
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.check()

    def test_rejects_changed_plan_digest(self):
        raw = json.dumps(self.plan).encode()
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            validate(self.plan, self.inventory, raw, self.inventory_bytes, '0' * 64, False,
                     now=self.now, hostname='lab-host')

    def test_rejects_path_traversal_in_record_name(self):
        self.plan['plan_id'] = '../outside'
        with self.assertRaisesRegex(ValueError, 'plan ID'):
            self.check()


if __name__ == '__main__':
    unittest.main()
