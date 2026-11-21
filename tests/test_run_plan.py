"""Verify that a reviewed plan cannot silently change its host or operation."""

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from tools.run_plan import digest, validate, run


class PlanValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.inventory = {'schema_version': 1, 'hostname': 'lab-host', 'observed_at': self.now.isoformat(),
                          'collection_status': 'complete', 'environment': {'kind': 'host_or_vm'},
                          'os_release': {'ID': 'ubuntu'}, 'kernel': '6.8', 'package_manager': 'dpkg',
                          'packages': {'status': 'complete'}, 'running_service_names': {'status': 'complete'}}
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

    def test_rejects_change_even_with_apply(self):
        self.plan['operations'] = [{'kind': 'compose-upgrade', 'target': 'app'}]
        with self.assertRaisesRegex(ValueError, 'disabled'):
            self.check()
        with self.assertRaisesRegex(ValueError, 'mutation disabled'):
            self.check(apply=True)

    def test_rejects_thin_or_container_inventory(self):
        for change in ({'packages': {}}, {'environment': {'kind': 'container'}}):
            previous = {key: self.inventory[key] for key in change}
            self.inventory.update(change)
            self.inventory_bytes = json.dumps(self.inventory).encode()
            self.plan['inventory_sha256'] = digest(self.inventory_bytes)
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                self.check()
            self.inventory.update(previous)

    def test_records_failed_and_pending_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, result = run(self.plan, self.inventory,
                               [['/bin/true'], ['/bin/false'], ['/bin/true']], Path(tmp), 'a' * 64)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual([s['status'] for s in result['steps']],
                             ['completed', 'failed', 'not_executed'])
            self.assertEqual(json.loads(path.read_text())['postconditions'], 'not_assessed')

    def test_signal_records_unknown_and_pending(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
          with self.subTest(signum=signum), tempfile.TemporaryDirectory() as tmp:
            runner = ("from tools.run_plan import run; from pathlib import Path; "
                      "run({'plan_id':'signal-test'}, {'hostname':'lab-host'}, "
                      "[['/bin/sleep','30'],['/bin/true']], Path(%r), 'a'*64)" % tmp)
            proc = subprocess.Popen([sys.executable, '-c', runner], cwd=Path(__file__).resolve().parents[1])
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    paths = list(Path(tmp).glob('signal-test-*.json'))
                    if paths and json.loads(paths[0].read_text())['steps'][0]['status'] == 'running':
                        break
                    time.sleep(.02)
                else:
                    self.fail('runner did not enter running state')
                proc.send_signal(signum)
                proc.wait(timeout=5)
                record = json.loads(paths[0].read_text())
                self.assertEqual(record['status'], 'interrupted')
                self.assertEqual([s['status'] for s in record['steps']],
                                 ['result_unknown', 'not_executed'])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

    def test_sigterm_escalates_if_child_ignores_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'ready'
            child_code = ("import signal,time; from pathlib import Path; "
                          "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                          f"Path({str(marker)!r}).write_text('ready'); time.sleep(30)")
            runner = ("from tools.run_plan import run; from pathlib import Path; "
                      f"run({{'plan_id':'stubborn'}}, {{'hostname':'lab-host'}}, "
                      f"[[{sys.executable!r},'-c',{child_code!r}]], Path({tmp!r}), 'a'*64)")
            proc = subprocess.Popen([sys.executable, '-c', runner], cwd=Path(__file__).resolve().parents[1])
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(marker.exists())
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=9)
                record = json.loads(next(Path(tmp).glob('stubborn-*.json')).read_text())
                self.assertEqual(record['steps'][0]['status'], 'result_unknown')
                self.assertEqual(record['status'], 'interrupted')
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

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
