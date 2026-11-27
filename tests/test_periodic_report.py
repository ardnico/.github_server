import unittest
from unittest.mock import patch
from pathlib import Path
import json
import smtplib
import tempfile
import sys
from tools.periodic_report import security_report, validate_mail_config, delivery_result, main


class ReportTests(unittest.TestCase):
    def test_unknown_distribution_is_unassessed(self):
        self.assertEqual(security_report('unknown')['status'], 'unassessed')

    def test_remote_plaintext_relay_rejected(self):
        with self.assertRaisesRegex(ValueError, 'smtp_starttls'):
            validate_mail_config({'smtp_host': 'mail.example.invalid', 'smtp_port': 25,
                                  'mail_from': 'ops@example.invalid', 'mail_to': ['ops@example.invalid']})

    def test_invalid_address_rejected(self):
        with self.assertRaisesRegex(ValueError, 'addresses'):
            validate_mail_config({'smtp_host': '127.0.0.1', 'smtp_port': 25,
                                  'mail_from': 'ops@example.invalid\nBcc: other@example.invalid',
                                  'mail_to': ['ops@example.invalid']})

    @patch('tools.periodic_report.shutil.which', return_value='/usr/bin/dnf')
    @patch('tools.periodic_report.subprocess.run')
    def test_warning_is_unassessed(self, command, _which):
        command.return_value.returncode = 0
        command.return_value.stdout = ''
        command.return_value.stderr = 'metadata is stale'
        result = security_report('rocky')
        self.assertEqual(result['status'], 'unassessed')
        self.assertIn('stale', result['error'])

    @patch('tools.periodic_report.performance_report', return_value={'status': 'collected'})
    @patch('tools.periodic_report.smtplib.SMTP')
    def test_smtp_refusal_is_recorded(self, smtp, _performance):
        smtp.return_value.__enter__.return_value.send_message.side_effect = smtplib.SMTPRecipientsRefused(
            {'ops@example.invalid': (550, b'rejected')})
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'mail.json'
            config.write_text(json.dumps({'smtp_host': '127.0.0.1', 'smtp_port': 2525,
                                          'mail_from': 'ops@example.invalid', 'mail_to': ['ops@example.invalid']}))
            with patch.object(sys, 'argv', ['periodic_report', '--kind', 'performance', '--output-dir',
                                           tmp, '--config', str(config), '--send']):
                self.assertEqual(main(), 3)
            report = json.loads(next(Path(tmp).glob('performance-*.json')).read_text())
            self.assertEqual(report['delivery']['status'], 'failed')
            self.assertEqual(report['delivery']['error_type'], 'SMTPRecipientsRefused')

    def test_partial_relay_acceptance_is_distinct_from_total_refusal(self):
        result = delivery_result({'no@example.invalid': (550, b'rejected')},
                                 ['yes@example.invalid', 'no@example.invalid'])
        self.assertEqual(result, {'status': 'partially_accepted_by_relay',
                                  'accepted_count': 1, 'refused_count': 1, 'refused_codes': {'550': 1}})


if __name__ == '__main__':
    unittest.main()
