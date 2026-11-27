#!/usr/bin/env python3
"""Publish a narrow Compose observation as root; read it without updater privileges."""

import argparse
from datetime import datetime, timezone
import grp
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import tomllib

DEFAULT_SNAPSHOT = Path('/var/lib/server-ops-kit/compose-state/snapshot.json')
DEFAULT_CONFIG = Path('/etc/svc-update/config.toml')
DEFAULT_UPDATER = Path('/usr/local/bin/svc-update')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
VERSION = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([a-z0-9][a-z0-9._-]*))?\Z')
STATUS = re.compile(r'([^: ]+): configured=([^, ]+), auto_update=(True|False), interrupted=(True|False)\Z')
CHECK = re.compile(r'([^: ]+): ([^ ]+) -> (.+)\Z')
MAX_AGE_SECONDS = 30 * 60
TIMER = 'server-ops-compose-snapshot.timer'
SERVICE = 'server-ops-compose-snapshot.service'


def version_parts(value):
    match = VERSION.fullmatch(value) if isinstance(value, str) and len(value) <= 128 else None
    if not match:
        raise ValueError('invalid updater version')
    return tuple(map(int, match.group(1, 2, 3))), match.group(4) or ''


def validate_directories(directory, *, require_root=True, stop_at=Path('/')):
    directory = directory.absolute()
    stop_at = stop_at.absolute()
    if not directory.is_relative_to(stop_at):
        raise PermissionError('snapshot is outside trusted directory chain')
    while True:
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022 or
                (require_root and info.st_uid != 0)):
            raise PermissionError(f'untrusted snapshot directory: {directory}')
        if directory == stop_at:
            break
        directory = directory.parent


def verify_publisher_state():
    active = subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet', TIMER],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=5, check=False)
    result = subprocess.run(['/usr/bin/systemctl', 'show', '--value', '--property=Result', SERVICE],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5, check=False)
    if active.returncode != 0 or result.returncode != 0 or result.stdout.strip() != 'success':
        raise ValueError('Compose snapshot timer stopped or publisher failed')


def observation(args, timeout=120):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, type(exc).__name__
    if result.returncode or result.stderr.strip() or len(result.stdout) > 32768:
        return None, f'exit {result.returncode} or diagnostic output'
    return result.stdout, None


def collect(config, updater, now=None):
    now = now or datetime.now(timezone.utc)
    settings = tomllib.loads(config.read_text(encoding='utf-8'))
    names = settings.get('services', {})
    if not isinstance(names, dict) or not names or len(names) > 128 or any(not NAME.fullmatch(name) for name in names):
        raise ValueError('invalid service names in updater configuration')
    prefix = [str(updater), '--config', str(config)]
    raw, error = observation([*prefix, 'status'])
    if error:
        raise ValueError(f'updater status unavailable: {error}')
    status = {}
    for line in raw.splitlines():
        match = STATUS.fullmatch(line)
        if match:
            name, version, auto, interrupted = match.groups()
            if name not in names or name in status:
                raise ValueError('unexpected updater status')
            version_parts(version)
            status[name] = {'enabled': True, 'configured': version,
                            'auto_update': auto == 'True', 'interrupted': interrupted == 'True'}
        elif line.endswith(': disabled') and line[:-10] in names and line[:-10] not in status:
            status[line[:-10]] = {'enabled': False}
        else:
            raise ValueError('unexpected updater status')
    if set(status) != set(names):
        raise ValueError('incomplete updater status')
    checks = {}
    for name, row in status.items():
        if not row['enabled'] or row['interrupted']:
            checks[name] = {'status': 'unassessed', 'reason': 'disabled or interrupted'}
            continue
        checked_at = datetime.now(timezone.utc).isoformat()
        raw, error = observation([*prefix, 'check', name])
        if error:
            checks[name] = {'status': 'unassessed', 'reason': error, 'observed_at': checked_at}
            continue
        match = CHECK.fullmatch(raw.strip()) if raw.count('\n') <= 1 else None
        if not match or match[1] != name or match[2] != row['configured']:
            checks[name] = {'status': 'unassessed', 'reason': 'unexpected updater check', 'observed_at': checked_at}
            continue
        candidate = match[3]
        if candidate != 'no eligible update':
            try:
                current_parts, flavor = version_parts(row['configured'])
                candidate_parts, candidate_flavor = version_parts(candidate)
                if candidate_parts <= current_parts or candidate_flavor != flavor:
                    raise ValueError('candidate is not a newer version in the same flavor')
            except ValueError:
                checks[name] = {'status': 'unassessed', 'reason': 'unexpected candidate', 'observed_at': checked_at}
                continue
        checks[name] = {'status': 'collected', 'current': match[2],
                        'candidate': None if candidate == 'no eligible update' else candidate,
                        'observed_at': checked_at}
    return {'schema_version': 1, 'hostname': socket.gethostname(),
            'status_observed_at': now.isoformat(), 'status': status, 'checks': checks}


def publish(snapshot, data, group):
    if os.geteuid() != 0:
        raise PermissionError('publisher must run as root')
    directory = snapshot.parent
    validate_directories(directory)
    gid = grp.getgrnam(group).gr_gid
    fd, temporary = tempfile.mkstemp(prefix='.compose-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            os.fchown(output.fileno(), 0, gid)
            os.fchmod(output.fileno(), 0o640)
            json.dump(data, output, ensure_ascii=False, sort_keys=True)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, snapshot)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_snapshot(snapshot=DEFAULT_SNAPSHOT, *, enforce_ownership=True, now=None):
    validate_directories(snapshot.parent, require_root=enforce_ownership,
                         stop_at=Path('/') if enforce_ownership else snapshot.parent)
    fd = os.open(snapshot, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 262144 or
                (enforce_ownership and info.st_uid != 0)):
            raise PermissionError('untrusted snapshot file')
        with os.fdopen(fd, 'r', encoding='utf-8') as source:
            fd = -1
            data = json.load(source)
    finally:
        if fd != -1:
            os.close(fd)
    now = now or datetime.now(timezone.utc)
    if data.get('schema_version') != 1 or data.get('hostname') != socket.gethostname():
        raise ValueError('snapshot schema or hostname mismatch')
    if data.get('publication_status') != 'complete':
        raise ValueError('snapshot publication is incomplete or failed')
    observed = datetime.fromisoformat(data['status_observed_at'])
    if observed.tzinfo is None or not 0 <= (now - observed).total_seconds() <= MAX_AGE_SECONDS:
        raise ValueError('snapshot expired or from the future')
    return data


def read_action(data, action, name=None, now=None):
    if action == 'status':
        return {'observed_at': data['status_observed_at'], 'services': data['status']}
    if not isinstance(name, str) or not NAME.fullmatch(name) or name not in data['checks']:
        raise ValueError('unknown snapshot service')
    row = data['checks'][name]
    if row.get('status') != 'collected':
        raise ValueError('service check is unassessed')
    now = now or datetime.now(timezone.utc)
    observed = datetime.fromisoformat(row['observed_at'])
    if observed.tzinfo is None or not 0 <= (now - observed).total_seconds() <= MAX_AGE_SECONDS:
        raise ValueError('service check expired or from the future')
    return {'service': name, **row}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    writer = sub.add_parser('publish')
    writer.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    writer.add_argument('--updater', type=Path, default=DEFAULT_UPDATER)
    writer.add_argument('--snapshot', type=Path, default=DEFAULT_SNAPSHOT)
    writer.add_argument('--reader-group', default='server-ops-reader')
    reader = sub.add_parser('read')
    reader.add_argument('kind', choices=('status', 'check'))
    reader.add_argument('service', nargs='?')
    args = parser.parse_args()
    if args.action == 'publish':
        if os.geteuid() != 0:
            raise PermissionError('publisher must run as root')
        publish(args.snapshot, {'schema_version': 1, 'hostname': socket.gethostname(),
                                'publication_status': 'unavailable'}, args.reader_group)
        data = collect(args.config, args.updater)
        data['publication_status'] = 'complete'
        publish(args.snapshot, data, args.reader_group)
        print(f"published snapshot for {len(data['status'])} services")
    else:
        if (args.kind == 'status' and args.service is not None) or (args.kind == 'check' and args.service is None):
            raise ValueError('invalid read action arguments')
        verify_publisher_state()
        print(json.dumps(read_action(read_snapshot(), args.kind, args.service), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, PermissionError, json.JSONDecodeError) as exc:
        print(f'compose snapshot unavailable: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
