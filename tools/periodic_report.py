#!/usr/bin/env python3
"""Create local OS advisory or performance snapshots, optionally email a summary."""

import argparse
from datetime import datetime, timezone
from email.message import EmailMessage
import json
import os
from pathlib import Path
import re
import shutil
import smtplib
import socket
import subprocess
import uuid
import tempfile


def os_id():
    for line in Path('/etc/os-release').read_text(encoding='utf-8').splitlines():
        if line.startswith('ID='):
            return line[3:].strip().strip('"').lower()
    return 'unknown'


def security_report(distro=None):
    distro = distro or os_id()
    if distro == 'ubuntu':
        command = ['pro', 'security-status', '--format', 'json']
        interpretation = 'Ubuntu package security coverage summary; not a complete list of affected CVEs'
    elif distro in ('rhel', 'rocky', 'almalinux', 'centos', 'fedora'):
        command = ['dnf', 'updateinfo', 'list', 'updates', 'security']
        interpretation = 'Available OS security advisories from configured repositories'
    elif distro == 'debian':
        command = ['debsecan', '--format', 'detail']
        interpretation = 'Debian package vulnerability data; check data freshness and distribution suite'
    else:
        return {'status': 'unassessed', 'reason': 'unsupported distribution', 'distro': distro}
    if not shutil.which(command[0]):
        return {'status': 'unassessed', 'reason': f'{command[0]} is not installed', 'distro': distro}
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False,
                                stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'status': 'unassessed', 'reason': type(exc).__name__, 'distro': distro}
    return {'status': 'collected' if result.returncode == 0 and not result.stderr.strip() else 'unassessed', 'distro': distro,
            'command': command, 'exit_code': result.returncode, 'interpretation': interpretation,
            'metadata_freshness': 'not independently verified',
            'output': result.stdout[:20000] if result.returncode == 0 else '',
            'error': result.stderr[:1000],
            'reason': 'advisory command emitted a warning; metadata or assessment may be incomplete' if result.stderr.strip() else None}


def performance_report():
    mem = {}
    for line in Path('/proc/meminfo').read_text(encoding='utf-8').splitlines():
        key, _, value = line.partition(':')
        if key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'):
            mem[key] = int(value.strip().split()[0]) * 1024
    disk = shutil.disk_usage('/')
    return {'status': 'collected', 'scope': 'one snapshot; no trend or latency diagnosis',
            'load_1_5_15': os.getloadavg(), 'memory_bytes': mem,
            'root_fs_bytes': {'total': disk.total, 'used': disk.used, 'free': disk.free}}


def validate_mail_config(config):
    required = ('smtp_host', 'smtp_port', 'mail_from', 'mail_to')
    if not isinstance(config, dict) or any(key not in config for key in required):
        raise ValueError('SMTP configuration is incomplete')
    if not isinstance(config['smtp_host'], str) or not config['smtp_host']:
        raise ValueError('invalid SMTP host')
    if not isinstance(config['smtp_port'], int) or not 1 <= config['smtp_port'] <= 65535:
        raise ValueError('invalid SMTP port')
    if not isinstance(config['mail_to'], list) or not config['mail_to']:
        raise ValueError('invalid mail addresses')
    addresses = [config['mail_from'], *config['mail_to']]
    if any(
        not isinstance(addr, str) or not re.fullmatch(r'[^\s@]+@[^\s@]+', addr) for addr in addresses
    ):
        raise ValueError('invalid mail addresses')
    if config['smtp_host'] not in ('localhost', '127.0.0.1', '::1') and not config.get('smtp_starttls'):
        raise ValueError('remote SMTP relay requires smtp_starttls=true')


def write_private(output_dir, report):
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    path = output_dir / f"{report['kind']}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return path


def update_private(path, report):
    fd, name = tempfile.mkstemp(prefix='.report-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def delivery_result(refused, recipients):
    accepted = len(recipients) - len(refused)
    codes = {}
    for _address, response in refused.items():
        code = response[0] if isinstance(response, (tuple, list)) and response else None
        codes[str(code) if isinstance(code, int) else 'unknown'] = codes.get(str(code) if isinstance(code, int) else 'unknown', 0) + 1
    return {'status': 'partially_accepted_by_relay' if accepted else 'failed',
            'accepted_count': accepted, 'refused_count': len(refused), 'refused_codes': codes}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kind', choices=('security', 'performance'), required=True)
    p.add_argument('--config', type=Path)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--send', action='store_true', help='email the summary via a configured relay')
    a = p.parse_args()
    if a.send and a.config is None:
        p.error('--send requires --config')
    config = json.loads(a.config.read_text(encoding='utf-8')) if a.config else None
    if a.send:
        validate_mail_config(config)
    report = {'kind': a.kind, 'hostname': socket.gethostname(), 'observed_at': datetime.now(timezone.utc).isoformat(),
              'result': security_report() if a.kind == 'security' else performance_report(),
              'delivery': {'status': 'pending' if a.send else 'not_requested'}}
    path = write_private(a.output_dir, report)
    print(f"saved {path}: {report['result']['status']}")
    if a.send:
        message = EmailMessage()
        message['Subject'] = f"[server-ops] {a.kind} {report['hostname']} {report['result']['status']}"
        message['From'] = config['mail_from']
        message['To'] = ', '.join(config['mail_to'])
        message.set_content(json.dumps(report, ensure_ascii=False, indent=2)[:25000])
        try:
            with smtplib.SMTP(config['smtp_host'], config['smtp_port'], timeout=15) as smtp:
                if config.get('smtp_starttls'):
                    smtp.starttls()
                refused = smtp.send_message(message)
            report['delivery'] = (delivery_result(refused, config['mail_to']) if refused else
                                  {'status': 'accepted_by_relay', 'accepted_count': len(config['mail_to']), 'refused_count': 0})
            print(f"email {report['delivery']['status']} (final delivery unverified)")
        except smtplib.SMTPRecipientsRefused as exc:
            report['delivery'] = delivery_result(exc.recipients, config['mail_to'])
            report['delivery']['error_type'] = 'SMTPRecipientsRefused'
            print(f"email delivery failed: SMTPRecipientsRefused; record: {path}")
        except (smtplib.SMTPException, OSError) as exc:
            report['delivery'] = {'status': 'failed', 'error_type': type(exc).__name__,
                                  'smtp_code': getattr(exc, 'smtp_code', None)}
            print(f"email delivery failed: {type(exc).__name__}; record: {path}")
        finally:
            update_private(path, report)
    if report['delivery']['status'] in ('failed', 'partially_accepted_by_relay'):
        return 3
    return 0 if report['result']['status'] == 'collected' else 2


if __name__ == '__main__':
    raise SystemExit(main())
