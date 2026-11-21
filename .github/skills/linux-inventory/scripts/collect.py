#!/usr/bin/env python3
"""Collect a small, local, read-only Linux inventory. Python 3.11+."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess


def os_release(path=Path('/etc/os-release')):
    result = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if re.fullmatch(r'[A-Z0-9_]+', key):
            parts = shlex.split(value, posix=True)
            result[key] = parts[0] if len(parts) == 1 else value
    return {key: result.get(key) for key in ('ID', 'ID_LIKE', 'VERSION_ID', 'PRETTY_NAME')}


def read_command(args):
    try:
        run = subprocess.run(args, text=True, capture_output=True, timeout=30, check=False)
        if run.returncode != 0:
            return {'command': args, 'status': 'unassessed', 'error': f'exit {run.returncode}', 'values': []}
        return {'command': args, 'status': 'complete', 'values': run.stdout.splitlines()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'command': args, 'status': 'unassessed', 'error': type(exc).__name__, 'values': []}


def environment():
    if Path('/.dockerenv').exists() or Path('/run/.containerenv').exists():
        return {'kind': 'container', 'host_scope': 'unassessed'}
    try:
        detected = subprocess.run(['systemd-detect-virt', '--container'], text=True,
                                  capture_output=True, timeout=5, check=False)
        if detected.returncode == 0:
            return {'kind': 'container', 'host_scope': 'unassessed', 'detector': 'systemd-detect-virt'}
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        cgroup = Path('/proc/1/cgroup').read_text(encoding='utf-8')
        if re.search(r'/(docker|libpod|kubepods|containerd)[/-]', cgroup):
            return {'kind': 'container', 'host_scope': 'unassessed'}
    except OSError:
        return {'kind': 'unknown', 'host_scope': 'unassessed'}
    try:
        init = Path('/proc/1/comm').read_text(encoding='utf-8').strip()
    except OSError:
        return {'kind': 'unknown', 'host_scope': 'unassessed'}
    if init not in ('systemd', 'init'):
        return {'kind': 'unknown', 'host_scope': 'unassessed', 'reason': 'PID 1 is not a known host init'}
    return {'kind': 'host_or_vm', 'host_scope': 'local'}


def collect():
    release = os_release()
    distro = (release['ID'] or '').lower()
    family = ((release['ID_LIKE'] or '') + ' ' + distro).lower().split()
    if {'debian', 'ubuntu'} & set(family):
        manager = 'dpkg'
        packages = read_command(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n'])
    elif {'rhel', 'fedora', 'centos', 'rocky', 'almalinux'} & set(family):
        manager = 'rpm'
        packages = read_command(['rpm', '-qa', '--qf', '%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}.%{ARCH}\n'])
    else:
        manager = 'unsupported'
        packages = {'command': None, 'status': 'unassessed', 'values': [], 'error': 'unsupported distribution'}
    services = read_command(['systemctl', 'list-units', '--type=service', '--state=running', '--no-legend', '--no-pager', '--plain'])
    # Keep unit names only. Descriptions and logs can contain private details.
    services['values'] = [line.split()[0] for line in services['values'] if line.split()]
    kernel = read_command(['uname', '-r'])
    env = environment()
    status = ('complete' if release['ID'] and env['kind'] == 'host_or_vm' and
              all(field['status'] == 'complete' for field in (packages, services, kernel)) else 'partial')
    return {
        'schema_version': 1,
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'hostname': socket.gethostname(),
        'os_release': release,
        'kernel': kernel['values'][0] if kernel['values'] else None,
        'kernel_observation': kernel,
        'environment': env,
        'collection_status': status,
        'package_manager': manager,
        'packages': packages,
        'running_service_names': services,
        'scope': 'container-local inventory and shared kernel; host outside container unassessed' if env['kind'] == 'container' else 'local host/VM; configuration contents, logs, secrets and network discovery excluded',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = collect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
        output.write('\n')
    args.output.chmod(0o600)
    print(f"saved {args.output}: {data['collection_status']} ({len(data['packages']['values'])} packages)")
    return 0 if data['collection_status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
