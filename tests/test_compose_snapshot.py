"""Snapshot is a bounded observation, never a path into the privileged updater."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import os
import socket
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

    def test_reader_rejects_stale_foreign_or_writable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'snapshot.json'
            now = datetime.now(timezone.utc)
            data = {'schema_version': 1, 'hostname': socket.gethostname(),
                    'status_observed_at': now.isoformat(), 'status': {}, 'checks': {}}
            file.write_text(json.dumps(data))
            self.assertEqual(mod.read_snapshot(file, enforce_ownership=False, now=now), data)
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
