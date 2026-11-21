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
import signal
import subprocess
import sys
import tempfile
import time
import uuid

TARGET = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,127}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')
SUPPORTED = {'service-status', 'compose-status', 'compose-check'}
MAX_AGE_SECONDS = 24 * 3600


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate(plan, inventory, plan_bytes, inventory_bytes, approved, apply, now=None, hostname=None):
    if apply:
        raise ValueError('mutation disabled: no separate approval and privilege broker is installed')
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
    if (inventory.get('collection_status') != 'complete' or
        inventory.get('environment', {}).get('kind') != 'host_or_vm' or
        not inventory.get('os_release', {}).get('ID') or not inventory.get('kernel') or
        inventory.get('package_manager') not in ('dpkg', 'rpm') or
        any(inventory.get(key, {}).get('status') != 'complete' for key in ('packages', 'running_service_names'))):
        raise ValueError('inventory is incomplete or target scope is not a host/VM')
    observed = datetime.fromisoformat(inventory['observed_at'].replace('Z', '+00:00'))
    if observed.tzinfo is None or not 0 <= (now - observed).total_seconds() <= MAX_AGE_SECONDS:
        raise ValueError('inventory is expired or its timestamp is invalid')
    operations = plan.get('operations')
    if not isinstance(operations, list) or not operations:
        raise ValueError('nonempty operations required')
    commands = []
    for item in operations:
        if not isinstance(item, dict) or item.get('kind') not in SUPPORTED:
            raise ValueError('unknown or disabled operation')
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
        commands.append(cmd)
    return commands


def redact(value):
    # Treat all captured output as private even after this best-effort redaction.
    return re.sub(r'(?im)\b(password|token|secret|api[_-]?key)\b\s*[:=]\s*\S+', r'\1=[REDACTED]', value)[:32768]


def save(path, result):
    fd, name = tempfile.mkstemp(prefix='.run-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(result, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(plan, inventory, commands, output_dir, plan_hash):
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    result = {'plan_id': plan['plan_id'], 'plan_sha256': plan_hash, 'hostname': inventory['hostname'],
              'started_at': datetime.now(timezone.utc).isoformat(),
              'steps': [{'command': cmd, 'status': 'not_executed'} for cmd in commands],
              'status': 'running', 'postconditions': 'not_assessed'}
    path = output_dir / f"{plan['plan_id']}-{uuid.uuid4().hex[:12]}.json"
    save(path, result)
    interrupted = []
    child = None

    def on_signal(signum, _frame):
        interrupted.append(signal.Signals(signum).name)
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for step in result['steps']:
            if interrupted:
                break
            step['status'] = 'running'
            step['started_at'] = datetime.now(timezone.utc).isoformat()
            save(path, result)
            try:
                child = subprocess.Popen(step['command'], text=True, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, start_new_session=True)
                if interrupted and child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                deadline = time.monotonic() + 1800
                interrupted_at = None
                while True:
                    try:
                        out, err = child.communicate(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        if interrupted and interrupted_at is None:
                            interrupted_at = time.monotonic()
                        if time.monotonic() >= deadline or (interrupted_at and time.monotonic() - interrupted_at >= 5):
                            os.killpg(child.pid, signal.SIGKILL)
                            out, err = child.communicate()
                            if not interrupted:
                                interrupted.append('timeout')
                            break
                step.update(exit_code=child.returncode, stdout=redact(out), stderr=redact(err))
                step['status'] = 'result_unknown' if interrupted else ('completed' if child.returncode == 0 else 'failed')
            except Exception as exc:
                step.update(exit_code=None, stdout='', stderr=type(exc).__name__, status='result_unknown')
            finally:
                child = None
                step['finished_at'] = datetime.now(timezone.utc).isoformat()
                save(path, result)
            if step['status'] != 'completed':
                break
        result['status'] = ('interrupted' if interrupted else
                            'completed' if all(s['status'] == 'completed' for s in result['steps']) else 'failed')
        if interrupted:
            result['interruption'] = interrupted[0]
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if result['status'] == 'running':
            result['status'] = 'result_unknown'
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        save(path, result)
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
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f'plan rejected: {exc}', file=sys.stderr)
        sys.exit(2)
