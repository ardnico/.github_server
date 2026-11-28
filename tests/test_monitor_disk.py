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


class HostResourceMonitorTest(unittest.TestCase):
    def test_cpu_baseline_gaps_and_reboot(self):
        entry = {}
        for minute, boot, busy, idle, expected in (
                ('00', 100, 100, 100, None),
                ('05', 100, 190, 110, 90.0),
                ('15', 100, 280, 120, None),
                ('20', 200, 300, 200, None),
                ('25', 200, 390, 210, 90.0)):
            with self.subTest(minute=minute):
                # A changing guest field must not inflate the usage delta.
                output = f'cpu {busy} 0 0 {idle} 0 0 0 0 {busy} 0\nbtime {boot}\n'
                at = f'2026-11-28T00:{minute}:00+00:00'
                sample = monitor.cpu_sample(output, entry, at, 420)
                self.assertEqual(sample and sample['used_percent'], expected)

    def test_independent_incidents_and_recovery_across_cron_ticks(self):
        target = {'id': 'lab', 'ssh_alias': 'lab-resource-monitor', 'assignee': 'ops',
                  'mail': {}, 'thresholds': {'cpu': 90, 'memory': 85}}
        cfg = {'resource_targets': [target], 'ssh_config': '/etc/monitor-ssh.conf',
               'consecutive_samples': 2, 'max_sample_gap_seconds': 420}
        readings = [
            ('00', 100, 100, 150),
            ('05', 190, 110, 150),
            ('10', 280, 120, 100),
            ('15', 370, 130, None),
            ('20', 460, 140, 300),
            ('25', 470, 230, 300),
        ]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(monitor, 'investigate', return_value={'status': 'disabled'}) as agent, \
                patch.object(monitor, 'notify', return_value={'status': 'accepted_by_relay'}) as mail:
            state_path = Path(directory) / 'state.json'
            for minute, busy, idle, available in readings:
                output = f'cpu {busy} 0 0 {idle} 0 0 0 0\nbtime 100\nMemTotal: 1000 kB\n'
                if available is not None:
                    output += f'MemAvailable: {available} kB\n'
                result = subprocess.CompletedProcess([], 0, output, '')
                with patch.object(monitor.subprocess, 'run', return_value=result) as run, \
                        patch.object(monitor, 'timestamp', return_value=f'2026-11-28T00:{minute}:00+00:00'):
                    self.assertEqual(monitor.tick(cfg, state_path, True), int(available is None))
                    run.assert_called_once()
                    command = run.call_args.args[0]
                    self.assertEqual(command[-2:], ['lab-resource-monitor',
                                                   'LC_ALL=C cat /proc/stat /proc/meminfo'])
                    self.assertIn('StrictHostKeyChecking=yes', command)
            state = json.loads(state_path.read_text())
            self.assertIsNone(state['lab:cpu']['active'])
            self.assertIsNone(state['lab:memory']['active'])
            cases = {case['kind']: case for p in state_path.parent.glob('*.json')
                     if p != state_path for case in [json.loads(p.read_text())]}
        self.assertEqual(agent.call_count, 2)
        self.assertEqual(mail.call_count, 3)
        self.assertEqual(set(cases), {'cpu_high', 'memory_high', 'collection_failed'})
        self.assertEqual(cases['cpu_high']['opened_at'][11:16], '00:10')
        self.assertEqual(cases['cpu_high']['closed_at'][11:16], '00:25')
        self.assertEqual(cases['memory_high']['opened_at'][11:16], '00:05')
        self.assertEqual(cases['memory_high']['closed_at'][11:16], '00:20')
        self.assertEqual(cases['collection_failed']['metric'], 'memory')
        self.assertEqual(cases['collection_failed']['closed_at'][11:16], '00:20')

    def test_resource_configuration_validation(self):
        cfg = {'consecutive_samples': 3, 'max_sample_gap_seconds': 420}
        target = {'id': 'lab', 'assignee': 'ops', 'thresholds': {'memory': 90}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({**cfg, 'resource_targets': [target]}))
            self.assertEqual(monitor.read_config(path)['resource_targets'], [target])
            for targets in ([target, target],
                            [{**target, 'thresholds': {'cpu': 101}}],
                            [{**target, 'thresholds': {'disk': 90}}]):
                path.write_text(json.dumps({**cfg, 'resource_targets': targets}))
                with self.assertRaises(ValueError):
                    monitor.read_config(path)
