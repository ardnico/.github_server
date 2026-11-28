"""Snapshot is a bounded observation, never a path into the privileged updater."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import compose_snapshot as mod


class SnapshotTests(unittest.TestCase):
    def test_collect_only_registered_names_and_narrow_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.toml'
            config.write_text('[services.app]\nenabled=true\n[services.other]\nenabled=false\n')
            def command(args):
                if args[-1] == 'status':
                    return 'app: configured=1.34.0, auto_update=False, interrupted=False\nother: disabled\n', None
                self.assertEqual(args[-2:], ['check', 'app'])
                return 'app: 1.34.0 -> 1.34.1\n', None
            with patch.object(mod, 'observation', side_effect=command):
                data = mod.collect(config, Path('/usr/local/bin/svc-update'))
            self.assertEqual(data['checks']['app']['candidate'], '1.34.1')
            self.assertEqual(data['checks']['other']['status'], 'unassessed')
            self.assertNotIn('enabled', data['checks']['app'])

    def test_failed_check_cannot_be_read_as_success(self):
        data = {'checks': {'app': {'status': 'unassessed'}}}
        with self.assertRaisesRegex(ValueError, 'unassessed'):
            mod.read_action(data, 'check', 'app')

    def test_updater_version_rules_reject_invalid_or_other_flavor(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.toml'
            config.write_text('[services.app]\nenabled=true\n')
            for candidate in ('latest', '01.34.1', '1.34.1+meta', '1.34.1-other', '1.34.0'):
                with self.subTest(candidate=candidate):
                    def command(args):
                        if args[-1] == 'status':
                            return 'app: configured=1.34.0-alpine, auto_update=False, interrupted=False\n', None
                        return f'app: 1.34.0-alpine -> {candidate}\n', None
                    with patch.object(mod, 'observation', side_effect=command):
                        data = mod.collect(config, Path('/usr/local/bin/svc-update'))
                    self.assertEqual(data['checks']['app']['status'], 'unassessed')

    def test_untrusted_ancestor_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            anchor = Path(tmp)
            parent = anchor / 'writable'
            child = parent / 'state'
            child.mkdir(parents=True)
            parent.chmod(0o770)
            with self.assertRaisesRegex(PermissionError, 'untrusted snapshot directory'):
                mod.validate_directories(child, require_root=False, stop_at=anchor)

    @patch('tools.compose_snapshot.os.geteuid', return_value=0)
    @patch('tools.compose_snapshot.publish')
    @patch('tools.compose_snapshot.collect', side_effect=ValueError('updater locked'))
    def test_failed_publication_invalidates_previous_candidate(self, _collect, publish, _uid):
        with patch.object(sys, 'argv', ['compose_snapshot', 'publish']):
            with self.assertRaisesRegex(ValueError, 'updater locked'):
                mod.main()
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(publish.call_args.args[1]['publication_status'], 'unavailable')

    @patch('tools.compose_snapshot.subprocess.run')
    def test_timer_and_publisher_must_be_healthy(self, run):
        run.side_effect = [type('Result', (), {'returncode': 0})(),
                           type('Result', (), {'returncode': 0, 'stdout': 'success\n'})()]
        mod.verify_publisher_state()
        run.side_effect = [type('Result', (), {'returncode': 3})(),
                           type('Result', (), {'returncode': 0, 'stdout': 'success\n'})()]
        with self.assertRaisesRegex(ValueError, 'timer stopped'):
            mod.verify_publisher_state()
        run.side_effect = [type('Result', (), {'returncode': 0})(),
                           type('Result', (), {'returncode': 0, 'stdout': 'exit-code\n'})()]
        with self.assertRaisesRegex(ValueError, 'publisher failed'):
            mod.verify_publisher_state()

    def test_reader_rejects_stale_foreign_or_writable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'snapshot.json'
            now = datetime.now(timezone.utc)
            data = {'schema_version': 1, 'hostname': socket.gethostname(),
                    'status_observed_at': now.isoformat(), 'status': {}, 'checks': {},
                    'publication_status': 'complete'}
            file.write_text(json.dumps(data))
            file.chmod(0o600)
            self.assertEqual(mod.read_snapshot(file, enforce_ownership=False, now=now), data)
            data['publication_status'] = 'unavailable'
            file.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                mod.read_snapshot(file, enforce_ownership=False, now=now)
            data['publication_status'] = 'complete'
            file.write_text(json.dumps(data))
            file.chmod(0o666)
            with self.assertRaises(PermissionError):
                mod.read_snapshot(file, enforce_ownership=False, now=now)
            file.chmod(0o600)
            data['status_observed_at'] = (now - timedelta(minutes=31)).isoformat()
            file.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'expired'):
                mod.read_snapshot(file, enforce_ownership=False, now=now)
            data['status_observed_at'] = now.isoformat()
            data['hostname'] = 'foreign'
            file.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'hostname'):
                mod.read_snapshot(file, enforce_ownership=False, now=now)

    def test_reader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'target'
            target.write_text('{}')
            link = Path(tmp) / 'snapshot.json'
            link.symlink_to(target)
            with self.assertRaises(OSError):
                mod.read_snapshot(link, enforce_ownership=False)
