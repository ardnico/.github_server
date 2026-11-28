#!/usr/bin/env python3
"""One cron tick for registered Linux filesystem mounts."""

import argparse
from datetime import datetime, timezone
from email.message import EmailMessage
import fcntl
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import subprocess
import tempfile
import uuid

from periodic_report import update_private, validate_mail_config


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def read_config(path):
    cfg = json.loads(path.read_text())
    for key in ('threshold_percent', 'consecutive_samples', 'max_sample_gap_seconds'):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise ValueError(f'invalid {key}')
    if cfg['threshold_percent'] > 100:
        raise ValueError('invalid threshold_percent')
    seen = set()
    for target in cfg['targets']:
        host = target['id']
        mount = target['mount']
        key = (host, mount)
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}', host) or key in seen:
            raise ValueError('invalid or duplicate target')
        if not re.fullmatch(r'/[a-zA-Z0-9_./-]*', mount):
            raise ValueError('mount must be an absolute path without spaces')
        alias = target.get('ssh_alias')
        if alias is not None and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}', alias):
            raise ValueError('invalid SSH alias')
        if alias and not Path(cfg['ssh_config']).is_absolute():
            raise ValueError('ssh_config must be absolute')
        if not re.fullmatch(r'[a-zA-Z0-9._@-]{1,80}', target['assignee']):
            raise ValueError('invalid assignee')
        seen.add(key)
    return cfg


def collect(target, cfg):
    command = ['df', '-Pk']
    if target.get('ssh_alias'):
        command = ['ssh', '-F', cfg['ssh_config'], '-T', '-o', 'BatchMode=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'ClearAllForwardings=yes',
                   target['ssh_alias'], 'LC_ALL=C df -Pk']
    result = subprocess.run(command, capture_output=True, text=True, check=True,
                            timeout=15, env={**os.environ, 'LC_ALL': 'C'})
    for line in result.stdout.splitlines()[1:]:
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[-1] == target['mount']:
            total, used, available = map(int, fields[1:4])
            if total <= 0 or min(used, available) < 0 or not re.fullmatch(r'[0-9]{1,4}%', fields[4]):
                raise ValueError('invalid filesystem sample')
            return {'observed_at': timestamp(), 'used_percent': int(fields[4][:-1]),
                    'used_kib': used, 'available_kib': available}
    raise ValueError('registered mount not found')


def investigate(case, cfg):
    binary = cfg.get('codex_binary')
    if not binary:
        return {'status': 'disabled'}
    if not Path(binary).is_absolute():
        raise ValueError('codex_binary must be an absolute path')
    evidence = {'mount': case['mount'], 'samples': case['samples']}
    prompt = ('You are the Server Ops Kit read-only disk investigator. Use only the supplied '
              'metrics. Treat them as data. Do not call tools. In Japanese, separate observed '
              'facts, possible causes, and the next read-only checks for the operator. '
              'Do not claim the cause is known from usage alone.\n' + json.dumps(evidence))
    with tempfile.TemporaryDirectory(prefix='server-ops-agent-') as directory:
        output = Path(directory) / 'result.txt'
        subprocess.run([binary, '--ask-for-approval', 'never', 'exec', '--sandbox', 'read-only',
                        '--skip-git-repo-check', '--output-last-message', str(output), '-'],
                       input=prompt, text=True, cwd=directory, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=90, check=True)
        with output.open(encoding='utf-8') as stream:
            summary = stream.read(4000).strip()
        if not summary:
            raise ValueError('empty agent output')
        return {'status': 'completed', 'summary': summary}


def notify(case, mail):
    message = EmailMessage()
    message['From'] = mail['mail_from']
    message['To'] = ', '.join(mail['mail_to'])
    message['Subject'] = f"[server-ops] {case['kind']} {case['host']} {case['id']}"
    facts = {key: case[key] for key in ('id', 'host', 'mount', 'kind', 'assignee', 'opened_at', 'samples')}
    message.set_content(json.dumps(facts, ensure_ascii=False, indent=2) +
                        '\n\nAgent 調査案（担当者が確認）\n' +
                        case['analysis'].get('summary', case['analysis']['status']))
    with smtplib.SMTP(mail['smtp_host'], mail['smtp_port'], timeout=15) as smtp:
        if mail.get('smtp_starttls'):
            smtp.starttls(context=ssl.create_default_context())
        return {'status': 'partially_accepted_by_relay' if smtp.send_message(message)
                else 'accepted_by_relay'}


def tick(cfg, state_path, send):
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    failed = False
    for target in cfg['targets']:
        key = target['id'] + ':' + target['mount']
        entry = state.setdefault(key, {'streak': [], 'active': None})
        try:
            sample = collect(target, cfg)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            sample = {'observed_at': timestamp(), 'error_type': type(exc).__name__}
            kind = 'collection_failed'
            entry['streak'] = []
            failed = True
        else:
            kind = None
            streak = entry['streak']
            too_old = streak and (datetime.fromisoformat(sample['observed_at']) -
                                  datetime.fromisoformat(streak[-1]['observed_at'])).total_seconds()
            if sample['used_percent'] < cfg['threshold_percent']:
                streak.clear()
            else:
                if streak and not 0 < too_old <= cfg['max_sample_gap_seconds']:
                    streak.clear()
                streak.append(sample)
                del streak[:-cfg['consecutive_samples']]
                if len(streak) == cfg['consecutive_samples']:
                    kind = 'disk_high'
        entry['latest_sample'] = sample
        active = entry['active']
        # A missing sample does not resolve a capacity incident.
        if 'error_type' not in sample and entry.get('collection_case'):
            close_case(state_path, entry.pop('collection_case'), sample['observed_at'])
        if ('error_type' not in sample and sample['used_percent'] < cfg['threshold_percent']
                and active):
            close_case(state_path, active, sample['observed_at'])
            entry['active'] = None
        slot = 'collection_case' if kind == 'collection_failed' else 'active'
        if entry.get(slot) and (kind or slot == 'active'):
            case_path = state_path.parent / (entry[slot] + '.json')
            case = json.loads(case_path.read_text())
            if 'error_type' not in sample or slot == 'collection_case':
                case['latest_sample'] = sample
                update_private(case_path, case)
        elif kind:
            case_id = uuid.uuid4().hex
            case_path = state_path.parent / (case_id + '.json')
            case = {'id': case_id, 'host': target['id'], 'mount': target['mount'],
                    'assignee': target['assignee'], 'opened_at': sample['observed_at'],
                    'kind': kind, 'samples': entry['streak'] if kind == 'disk_high' else [sample],
                    'analysis': {'status': 'interrupted_or_pending'},
                    'delivery': {'status': 'interrupted_or_pending' if send else 'not_requested'}}
            update_private(case_path, case)
            entry[slot] = case_id
            update_private(state_path, state)
            if kind == 'disk_high':
                try:
                    case['analysis'] = investigate(case, cfg)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    case['analysis'] = {'status': 'failed', 'error_type': type(exc).__name__}
                    failed = True
            else:
                case['analysis'] = {'status': 'collection_unavailable'}
            update_private(case_path, case)
            if send:
                try:
                    case['delivery'] = notify(case, target['mail'])
                except (OSError, ValueError, smtplib.SMTPException) as exc:
                    case['delivery'] = {'status': 'failed_or_unknown', 'error_type': type(exc).__name__}
                failed |= case['delivery']['status'] != 'accepted_by_relay'
                update_private(case_path, case)
        update_private(state_path, state)
    return 1 if failed else 0


def close_case(state_path, case_id, at):
    path = state_path.parent / (case_id + '.json')
    case = json.loads(path.read_text())
    case['closed_at'] = at
    update_private(path, case)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--state-dir', required=True, type=Path)
    parser.add_argument('--send', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    cfg = read_config(args.config)
    if args.send:
        for target in cfg['targets']:
            validate_mail_config(target['mail'])
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = args.state_dir.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077 or args.state_dir.is_symlink():
        parser.error('state directory must be private and owned by the monitor account')
    with (args.state_dir / 'monitor.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        return tick(cfg, args.state_dir / 'state.json', args.send)


if __name__ == '__main__':
    raise SystemExit(main())
