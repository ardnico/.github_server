#!/usr/bin/env python3
"""Execute a reviewed, host-bound plan through a fixed operation list."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import uuid

TARGET = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,127}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')
SUPPORTED = {'service-status', 'compose-status', 'compose-check', 'compose-upgrade'}
MAX_AGE_SECONDS = 24 * 3600


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate(plan, inventory, plan_bytes, inventory_bytes, approved, apply, now=None, hostname=None):
    now = now or datetime.now(timezone.utc)
    hostname = hostname or socket.gethostname()
    if not SHA.fullmatch(approved or '') or approved != digest(plan_bytes):
        raise ValueError('plan SHA-256 does not match the final reviewed file')
    if plan.get('schema_version') != 1 or inventory.get('schema_version') != 1:
        raise ValueError('unsupported schema')
    if not isinstance(plan.get('plan_id'), str) or not TARGET.fullmatch(plan['plan_id']):
        raise ValueError('invalid plan ID')
    if plan.get('hostname') != hostname or inventory.get('hostname') != hostname:
        raise ValueError('host mismatch')
    if plan.get('inventory_sha256') != digest(inventory_bytes):
        raise ValueError('inventory hash mismatch')
    observed = datetime.fromisoformat(inventory['observed_at'].replace('Z', '+00:00'))
    if observed.tzinfo is None or not 0 <= (now - observed).total_seconds() <= MAX_AGE_SECONDS:
        raise ValueError('inventory is expired or its timestamp is invalid')
    operations = plan.get('operations')
    if not isinstance(operations, list) or not operations:
        raise ValueError('nonempty operations required')
    commands = []
    for item in operations:
        if not isinstance(item, dict) or item.get('kind') not in SUPPORTED:
            raise ValueError('unknown operation')
        kind = item['kind']
        if kind == 'compose-status':
            if set(item) != {'kind'}:
                raise ValueError('unexpected operation parameters')
            cmd = ['svc-update', 'status']
        else:
            if set(item) != {'kind', 'target'} or not isinstance(item['target'], str) or not TARGET.fullmatch(item['target']):
                raise ValueError('invalid target or parameters')
            if kind == 'service-status':
                cmd = ['systemctl', 'status', '--no-pager', '--lines=0', item['target']]
            elif kind == 'compose-check':
                cmd = ['svc-update', 'check', item['target']]
            else:
                if not apply:
                    raise ValueError('compose-upgrade requires --apply')
                cmd = ['svc-update', 'upgrade', item['target'], '--apply']
        commands.append(cmd)
    return commands


def redact(value):
    # Treat all captured output as private even after this best-effort redaction.
    return re.sub(r'(?im)\b(password|token|secret|api[_-]?key)\b\s*[:=]\s*\S+', r'\1=[REDACTED]', value)[:32768]


def run(plan, inventory, commands, output_dir, plan_hash):
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    result = {'plan_id': plan['plan_id'], 'plan_sha256': plan_hash, 'hostname': inventory['hostname'],
              'started_at': datetime.now(timezone.utc).isoformat(), 'steps': [], 'status': 'running'}
    path = output_dir / f"{plan['plan_id']}-{uuid.uuid4().hex[:12]}.json"
    try:
        for cmd in commands:
            started = datetime.now(timezone.utc).isoformat()
            try:
                completed = subprocess.run(cmd, text=True, capture_output=True, timeout=1800, check=False,
                                           stdin=subprocess.DEVNULL)
                step = {'command': cmd, 'started_at': started, 'exit_code': completed.returncode,
                        'stdout': redact(completed.stdout), 'stderr': redact(completed.stderr)}
            except (OSError, subprocess.TimeoutExpired) as exc:
                step = {'command': cmd, 'started_at': started, 'exit_code': None,
                        'stdout': '', 'stderr': type(exc).__name__}
            step['finished_at'] = datetime.now(timezone.utc).isoformat()
            result['steps'].append(step)
            if step['exit_code'] != 0:
                result['status'] = 'failed; remaining operations not executed'
                break
        else:
            result['status'] = 'commands completed; verify postconditions separately'
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write('\n')
    return path, result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', required=True, type=Path)
    p.add_argument('--inventory', required=True, type=Path)
    p.add_argument('--confirm-sha', required=True)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--output-dir', type=Path, default=Path('.server-ops/runs'))
    a = p.parse_args()
    plan_bytes, inventory_bytes = a.plan.read_bytes(), a.inventory.read_bytes()
    plan, inventory = json.loads(plan_bytes), json.loads(inventory_bytes)
    commands = validate(plan, inventory, plan_bytes, inventory_bytes, a.confirm_sha, a.apply)
    path, result = run(plan, inventory, commands, a.output_dir, digest(plan_bytes))
    print(f"{result['status']}; record: {path}")
    return 0 if result['status'].startswith('commands completed') else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f'plan rejected: {exc}', file=sys.stderr)
        sys.exit(2)
