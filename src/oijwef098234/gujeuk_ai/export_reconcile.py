"""Offline reconciliation of explicitly approved public Notion export slices.

The approval file is independent evidence from a public observation. A trusted
page ID alone never authorizes an export field or a database row. No browser,
network client, model, or recursive export traversal is used here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from .sources import notion_id, notion_url, page_id
from .wiki import WikiWorkspace, private_write

MAX_FILE = 1024 * 1024
MAX_FIELDS = 40
MAX_ROWS = 200
MAX_VALUE = 4000
SENSITIVE = re.compile(
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:gh[pousr]_|github_pat_|sk-(?:proj-)?)[A-Za-z0-9_-]{20,}'
    r'|AKIA[0-9A-Z]{16}|\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S+'
    r'|\bBearer\s+[A-Za-z0-9._~-]{12,}|\bxox[baprs]-[A-Za-z0-9-]{12,}'
    r'|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|(?<!\d)01[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)'
    r'|(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)|주민등록|계좌번호|전화번호|건강정보|의료정보', re.I)
PERSON_FIELD = re.compile(r'^(?:owner|assignee|author|person|email|phone|address|birthdate|담당자|작성자|이름|연락처|주소|생년월일)$', re.I)
CREDENTIAL_FIELD = re.compile(r'token|secret|password|credential|api[_ -]?key|private[_ -]?key|access[_ -]?key|비밀|암호|인증', re.I)


def _object(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('invalid_export_schema')
    return value


def _private_directory(path):
    path = Path(path)
    if path.is_symlink() or any(part.is_symlink() for part in path.parents):
        raise ValueError('export_symlink_rejected')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def _read_json(path):
    path = Path(path)
    if path.is_symlink() or any(part.is_symlink() for part in path.parents) or not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError('export_file_rejected')
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise ValueError('export_file_rejected')
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_export_key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except (UnicodeError, ValueError):
        raise ValueError('invalid_export_json') from None


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('invalid_public_observation')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('invalid_public_observation') from None
    if parsed.tzinfo is None or parsed > datetime.now(timezone.utc):
        raise ValueError('invalid_public_observation')
    return parsed.astimezone(timezone.utc).isoformat()


def _approved_value(value, evidence):
    _object(evidence, ('sha256', 'observed_at'))
    evidence['observed_at'] = _timestamp(evidence['observed_at'])
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_VALUE:
        raise ValueError('invalid_public_value')
    if SENSITIVE.search(value):
        raise ValueError('sensitive_export_value_rejected')
    digest = hashlib.sha256(value.encode()).hexdigest()
    if not isinstance(evidence['sha256'], str) or evidence['sha256'] != digest:
        raise ValueError('public_evidence_mismatch')
    return value


def _fields(values, approvals):
    if not isinstance(values, dict) or not isinstance(approvals, dict) or len(approvals) > MAX_FIELDS:
        raise ValueError('invalid_export_fields')
    selected = {}
    for name, evidence in approvals.items():
        if (not isinstance(name, str) or not 1 <= len(name) <= 100 or SENSITIVE.search(name)
                or PERSON_FIELD.search(name) or CREDENTIAL_FIELD.search(name)):
            raise ValueError('invalid_public_field')
        if name not in values:
            raise ValueError('approved_field_missing')
        selected[name] = _approved_value(values[name], evidence)
    return selected


def _document(page, row_id, fields, approvals, url, modified):
    observations = [e['observed_at'] for e in approvals.values()]
    if min(observations) < modified:
        raise ValueError('public_evidence_stale')
    observed = max(observations)
    content = '\n'.join(f'{key}: {value}' for key, value in sorted(fields.items()))
    revision = hashlib.sha256(json.dumps({'content': content, 'observed': observed,
        'modified': modified, 'permission': 'public_at_observation'}, sort_keys=True).encode()).hexdigest()
    return {'kind': 'notion', 'id': 'local-export:' + page + (':' + row_id if row_id else ''),
        'url': url, 'title': fields.get('Title') or fields.get('Name') or '공개 Notion 자료',
        'content': content, 'observed_at': observed, 'source_updated_at': modified,
        'permission': 'public_at_observation', 'revision': revision}


def reconcile_export(store, export_root, approval_path, stage_root, *, trusted_page_ids):
    """Stage and atomically reconcile approved slices into a local WikiWorkspace.

    Export files must be explicit JSON fixtures of {id, modified_at, fields,
    rows:[{id, fields}]}. The approval file has {version:1, pages:[{id,url,
    file, fields, rows:[{id, fields}]}]}; each approved field maps to a
    {sha256, observed_at} public observation. Unlisted values never enter the
    stage, database, or inference path.
    """
    if not isinstance(trusted_page_ids, (set, frozenset, tuple, list)) or not trusted_page_ids:
        raise ValueError('trusted_page_ids_required')
    trusted = {notion_id(value) for value in trusted_page_ids}
    if '' in trusted or len(trusted) != len(trusted_page_ids):
        raise ValueError('invalid_trusted_page_ids')
    export_root = Path(export_root)
    if not export_root.is_dir() or export_root.is_symlink() or any(part.is_symlink() for part in export_root.parents):
        raise ValueError('export_root_rejected')
    export_root = export_root.resolve()
    stage_candidate = Path(stage_root).resolve()
    if stage_candidate == export_root or export_root in stage_candidate.parents or stage_candidate in export_root.parents:
        raise ValueError('stage_must_be_separate')
    manifest = _read_json(approval_path)
    _object(manifest, ('version', 'pages'))
    if manifest['version'] != 1 or not isinstance(manifest['pages'], list) or len(manifest['pages']) > 200:
        raise ValueError('invalid_approval_manifest')
    docs, seen = [], set()
    for approval in manifest['pages']:
        _object(approval, ('id', 'url', 'file', 'fields', 'rows'))
        page = notion_id(approval['id'])
        if not page or page not in trusted or page in seen or not isinstance(approval['url'], str):
            raise ValueError('untrusted_page_id')
        seen.add(page)
        url = notion_url(approval['url'])
        if page_id(url) != page or url != approval['url']:
            raise ValueError('public_page_url_mismatch')
        relative = approval['file']
        if not isinstance(relative, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.json', relative) or relative.startswith('.'):
            raise ValueError('export_path_rejected')
        exported = _read_json(export_root / relative)
        _object(exported, ('id', 'modified_at', 'fields', 'rows'))
        if notion_id(exported['id']) != page or not isinstance(exported['rows'], list) or len(exported['rows']) > MAX_ROWS:
            raise ValueError('export_page_mismatch')
        modified = _timestamp(exported['modified_at'])
        fields = _fields(exported['fields'], approval['fields'])
        if fields:
            docs.append(_document(page, '', fields, approval['fields'], url, modified))
        if not isinstance(approval['rows'], list) or len(approval['rows']) > MAX_ROWS:
            raise ValueError('invalid_public_rows')
        rows = {}
        for row in exported['rows']:
            _object(row, ('id', 'fields'))
            row_id = notion_id(row['id'])
            if not row_id or row_id in rows:
                raise ValueError('invalid_export_row')
            rows[row_id] = row['fields']
        approved_rows = set()
        for approved in approval['rows']:
            _object(approved, ('id', 'fields'))
            row_id = notion_id(approved['id'])
            if not row_id or row_id in approved_rows or row_id not in rows:
                raise ValueError('approved_row_missing')
            approved_rows.add(row_id)
            selected = _fields(rows[row_id], approved['fields'])
            if not selected:
                raise ValueError('approved_row_without_fields')
            docs.append(_document(page, row_id, selected, approved['fields'], url, modified))
    # All validation completes before stage or database mutation. Never stage a
    # raw export tree; the only durable copy contains approved field slices.
    staged = {'version': 1, 'documents': docs, 'complete': False,
        'limits': ['Only explicitly approved public fields and rows were imported.']}
    stage_root = _private_directory(stage_root)
    private_write(stage_root, 'approved.json', json.dumps(staged, ensure_ascii=False, indent=2))
    store.db.execute('CREATE TABLE IF NOT EXISTS local_export_docs(id TEXT PRIMARY KEY)')
    old = {row[0] for row in store.db.execute('SELECT id FROM local_export_docs')}
    new = {doc['id'] for doc in docs}
    changed = 0
    with store.db:
        for doc in docs:
            raw = json.dumps(doc, ensure_ascii=False)
            stable = {k: doc.get(k) for k in ('kind', 'id', 'url', 'title', 'content', 'author_id', 'revision')}
            digest = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            previous = store.db.execute('SELECT digest FROM documents WHERE kind=? AND id=?',
                ('notion', doc['id'])).fetchone()
            if previous is None or previous[0] != digest:
                store.db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?)', ('notion', doc['id'], raw, digest))
                changed += 1
            store.db.execute('INSERT OR IGNORE INTO local_export_docs VALUES(?)', (doc['id'],))
        for ident in old - new:
            store.db.execute('DELETE FROM documents WHERE kind=? AND id=?', ('notion', ident))
            store.db.execute('DELETE FROM local_export_docs WHERE id=?', (ident,))
    return {'approved_documents': len(docs), 'withdrawn_documents': len(old - new),
        'changed_documents': changed,
        'complete': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-root', type=Path, required=True)
    parser.add_argument('--approval', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--trusted-page-id', action='append', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    store = WikiWorkspace(args.state)
    try:
        print(json.dumps(reconcile_export(store, args.export_root, args.approval, args.stage,
            trusted_page_ids=args.trusted_page_id)))
    finally:
        store.close()


if __name__ == '__main__':
    main()
