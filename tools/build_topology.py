#!/usr/bin/env python3
"""Build evidence-bound topology data and a human summary from one host inventory."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re


ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z')
TEXT = re.compile(r'[\w .:/@()\-]{1,160}\Z', re.UNICODE)
KINDS = {'config-observed', 'user-confirmed', 'hypothesis'}
TYPES = {'host', 'service', 'storage', 'external'}
RELATIONS = {'runs-on', 'depends-on', 'connects-to', 'stores-in'}


def check_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('invalid topology ID')
    return value


def check_text(value):
    if not isinstance(value, str) or not TEXT.fullmatch(value):
        raise ValueError('invalid topology label or source reference')
    return value


def evidence(value):
    if not isinstance(value, dict) or set(value) != {'kind', 'source_ref', 'observed_at'}:
        raise ValueError('evidence requires kind, source_ref and observed_at')
    if value['kind'] not in KINDS:
        raise ValueError('invalid evidence kind')
    check_text(value['source_ref'])
    try:
        stamp = datetime.fromisoformat(value['observed_at'].replace('Z', '+00:00'))
    except (AttributeError, ValueError) as exc:
        raise ValueError('invalid evidence timestamp') from exc
    if stamp.tzinfo is None:
        raise ValueError('evidence timestamp needs timezone')
    return value


def build(inventory_bytes, inventory_path, claims=None):
    inventory = json.loads(inventory_bytes)
    if (inventory.get('schema_version') != 1 or inventory.get('collection_status') != 'complete' or
            inventory.get('environment', {}).get('kind') != 'host_or_vm' or
            inventory.get('running_service_names', {}).get('status') != 'complete'):
        raise ValueError('complete local host inventory required')
    host = check_id(inventory.get('hostname'))
    observed = inventory.get('observed_at')
    evidence({'kind': 'config-observed', 'source_ref': 'inventory', 'observed_at': observed})
    names = inventory['running_service_names']['values']
    if not isinstance(names, list) or any(not isinstance(n, str) or not ID.fullmatch(n) for n in names):
        raise ValueError('invalid service names')
    if len(names) != len(set(names)):
        raise ValueError('duplicate service names')
    inv_hash = hashlib.sha256(inventory_bytes).hexdigest()
    if claims is None:
        claims = {'schema_version': 1, 'hostname': host, 'inventory_sha256': inv_hash,
                  'nodes': [], 'edges': []}
    if (not isinstance(claims, dict) or set(claims) !=
            {'schema_version', 'hostname', 'inventory_sha256', 'nodes', 'edges'} or
            claims['schema_version'] != 1 or claims['hostname'] != host or
            claims['inventory_sha256'] != inv_hash):
        raise ValueError('claims do not match inventory and host')
    if not isinstance(claims['nodes'], list) or not isinstance(claims['edges'], list):
        raise ValueError('nodes and edges must be lists')
    nodes = [{'id': host, 'type': 'host', 'label': host, 'evidence':
              {'kind': 'config-observed', 'source_ref': 'inventory', 'observed_at': observed}}]
    nodes += [{'id': 'service:' + n, 'type': 'service', 'label': n, 'evidence':
               {'kind': 'config-observed', 'source_ref': 'inventory', 'observed_at': observed}}
              for n in sorted(names)]
    ids = {node['id'] for node in nodes}
    for node in claims['nodes']:
        if not isinstance(node, dict) or set(node) != {'id', 'type', 'label', 'evidence'}:
            raise ValueError('invalid node fields')
        node_id = check_id(node['id'])
        if node_id in ids or node['type'] not in TYPES:
            raise ValueError('duplicate node or invalid node type')
        check_text(node['label'])
        evidence(node['evidence'])
        nodes.append(node)
        ids.add(node_id)
    edges = [{'from': 'service:' + n, 'to': host, 'relationship': 'runs-on',
              'evidence': {'kind': 'config-observed', 'source_ref': 'inventory', 'observed_at': observed}}
             for n in sorted(names)]
    edge_keys = {(e['from'], e['to'], e['relationship']) for e in edges}
    for edge in claims['edges']:
        if not isinstance(edge, dict) or set(edge) != {'from', 'to', 'relationship', 'evidence'}:
            raise ValueError('invalid edge fields')
        src, dst = check_id(edge['from']), check_id(edge['to'])
        key = (src, dst, edge['relationship'])
        if (src not in ids or dst not in ids or src == dst or
                edge['relationship'] not in RELATIONS or key in edge_keys):
            raise ValueError('invalid, duplicate or dangling edge')
        evidence(edge['evidence'])
        edges.append(edge)
        edge_keys.add(key)
    return {'schema_version': 1, 'hostname': host, 'inventory_path': str(inventory_path),
            'inventory_sha256': inv_hash, 'observed_at': observed,
            'nodes': nodes, 'edges': edges,
            'claim_node_ids': [node['id'] for node in claims['nodes']],
            'unassessed': ['service dependencies not covered by claims',
                           'network paths and external reachability',
                           'backup and recovery paths']}


def render(topology):
    host = topology['hostname']
    claim_ids = set(topology.get('claim_node_ids', []))
    services = [n for n in topology['nodes'] if n['type'] == 'service' and n['id'] not in claim_ids]
    claim_nodes = [n for n in topology['nodes'] if n['id'] in claim_ids]
    manual = [e for e in topology['edges'] if e['relationship'] != 'runs-on']
    lines = [f'# {host} の構成', '', f'台帳取得時刻: {topology["observed_at"]}',
             f'台帳 SHA-256: `{topology["inventory_sha256"]}`',
             'この図は対象 VM で観測したサービスと、根拠を別途登録した関係だけを示す。', '',
             '## 稼働サービス（台帳取得時点）', '',
             ', '.join(f'`{n["label"]}`' for n in services) or 'なし', '',
             '## 関係図', '']
    if manual:
        ids = {n['id']: n for n in topology['nodes']}
        selected = {host} | {e[x] for e in manual for x in ('from', 'to')}
        lines += ['```mermaid', 'flowchart TD']
        key = {v: f'n{i}' for i, v in enumerate(sorted(selected))}
        for name in sorted(selected):
            lines.append(f'  {key[name]}["{ids[name]["label"]}"]')
        for e in manual:
            arrow = '-.->' if e['evidence']['kind'] == 'hypothesis' else '-->'
            lines.append(f'  {key[e["from"]]} {arrow}|{e["relationship"]}| {key[e["to"]]}')
        lines += ['```', '', '破線は仮説。実線にも根拠の種類と時刻を下表で確認する。']
    else:
        lines.append('未作成。依存・通信・保存先の関係は未登録。サービス名から推測して線を引かない。')
    if claim_nodes:
        lines += ['', '## 追加ノードの根拠', '',
                  '| ノード ID | 表示名 | 種類 | 根拠 | 取得時刻 |',
                  '| --- | --- | --- | --- | --- |']
        for node in claim_nodes:
            ev = node['evidence']
            lines.append(f'| `{node["id"]}` | {node["label"]} | {node["type"]} | '
                         f'{ev["kind"]}: {ev["source_ref"]} | {ev["observed_at"]} |')
    lines += ['', '## 関係の根拠', '', '| 始点 → 終点 | 関係 | 根拠 | 取得時刻 |',
              '| --- | --- | --- | --- |']
    for e in manual:
        ev = e['evidence']
        lines.append(f'| `{e["from"]}` → `{e["to"]}` | {e["relationship"]} | '
                     f'{ev["kind"]}: {ev["source_ref"]} | {ev["observed_at"]} |')
    if not manual:
        lines.append('| 未登録 | 未確認 | 未確認 | - |')
    lines += ['', '## 未評価', ''] + [f'- {item}' for item in topology['unassessed']]
    return '\n'.join(lines) + '\n'


def create_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_mode & 0o077:
        raise ValueError('output directory must be private (0700)')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True, type=Path)
    parser.add_argument('--claims', type=Path, help='optional reviewed claims JSON')
    parser.add_argument('--output-prefix', required=True, type=Path)
    args = parser.parse_args()
    if args.output_prefix.suffix:
        parser.error('output prefix must not have a suffix')
    topology = build(args.inventory.read_bytes(), args.inventory, json.loads(args.claims.read_text()) if args.claims else None)
    json_path = args.output_prefix.with_suffix('.json')
    md_path = args.output_prefix.with_suffix('.md')
    if json_path.exists() or md_path.exists():
        parser.error('output already exists')
    create_private(json_path, json.dumps(topology, ensure_ascii=False, indent=2) + '\n')
    create_private(md_path, render(topology))
    print(f'saved {json_path} and {md_path}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f'topology rejected: {exc}') from exc
