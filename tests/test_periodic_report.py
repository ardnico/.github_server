import unittest
from tools.periodic_report import security_report, validate_mail_config


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


if __name__ == '__main__':
    unittest.main()
