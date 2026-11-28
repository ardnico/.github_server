import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import monitor_disk as monitor


class DiskMonitorTest(unittest.TestCase):
    def test_fixed_ssh_collection(self):
        target = {'ssh_alias': 'lab-monitor', 'mount': '/'}
        result = subprocess.CompletedProcess([], 0, 'header\n/dev/vda 100 96 4 96% /\n', '')
        with patch.object(monitor.subprocess, 'run', return_value=result) as run:
            sample = monitor.collect(target, {'ssh_config': '/etc/monitor-ssh.conf'})
        self.assertEqual(sample['used_percent'], 96)
        self.assertEqual(run.call_args.args[0][-2:], ['lab-monitor', 'LC_ALL=C df -Pk'])
        self.assertIn('StrictHostKeyChecking=yes', run.call_args.args[0])

    def test_threshold_dedup_agent_failure_and_collection_failure(self):
        target = {'id': 'lab', 'mount': '/', 'assignee': 'ops', 'mail': {}}
        cfg = {'targets': [target], 'threshold_percent': 90, 'consecutive_samples': 2,
               'max_sample_gap_seconds': 420}
        samples = [{'observed_at': f'2026-11-28T00:{minute}:00+00:00', 'used_percent': percent}
                   for minute, percent in [('00', 90), ('05', 91), ('10', 95), ('20', 80)]]
        sequence = [*samples[:3], OSError('network'), samples[-1]]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(monitor, 'collect', side_effect=sequence), \
                patch.object(monitor, 'investigate', side_effect=subprocess.TimeoutExpired('codex', 90)) as agent, \
                patch.object(monitor, 'notify', return_value={'status': 'accepted_by_relay'}) as mail:
            state_path = Path(directory) / 'state.json'
            self.assertEqual([monitor.tick(cfg, state_path, True) for _ in sequence], [0, 1, 0, 1, 0])
            cases = {case['kind']: case for p in state_path.parent.glob('*.json')
                     if p != state_path for case in [json.loads(p.read_text())]}
            self.assertEqual(agent.call_count, 1)
            self.assertEqual(mail.call_count, 2)
            self.assertEqual(cases['disk_high']['analysis']['status'], 'failed')
            self.assertEqual(cases['disk_high']['latest_sample']['used_percent'], 95)
            self.assertEqual(cases['disk_high']['delivery']['status'], 'accepted_by_relay')
            self.assertIn('closed_at', cases['disk_high'])
            self.assertIn('closed_at', cases['collection_failed'])
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)
