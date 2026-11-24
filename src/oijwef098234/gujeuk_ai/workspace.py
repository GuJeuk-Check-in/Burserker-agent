"""Server-owned draft workspace, evidence cache and idempotent owner commands.

Notion remains read-only authority; approvals change this workspace, not Notion.
All SQL lives in this Store subclass, never the transport/UI.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re
import sqlite3
from .db import Store, validate
from .sources import now, notion_url, GITHUB_ORG
from urllib.parse import urlparse


class Workspace(Store):
    def __init__(self, path=None):
        super().__init__(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS documents(
                kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, digest TEXT NOT NULL,
                PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS collection_status(kind TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS model_runs(digest TEXT PRIMARY KEY, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notion_frontier(
                url TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'pending',
                next_try REAL NOT NULL DEFAULT 0, checked REAL, error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS workspace_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        if not self.db.execute("SELECT 1 FROM workspace_meta WHERE key='document_digest_v2'").fetchone():
            with self.db:
                for row in self.db.execute('SELECT kind,id,data FROM documents'):
                    data = json.loads(row[2])
                    stable = {k: data.get(k) for k in ('kind', 'id', 'url', 'title', 'content', 'author_id', 'revision')}
                    digest = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    self.db.execute('UPDATE documents SET digest=? WHERE kind=? AND id=?', (digest,row[0],row[1]))
                self.db.execute("INSERT INTO workspace_meta VALUES('document_digest_v2','1')")

    def ingest(self, snapshot):
        rows, coverage = snapshot['documents'], snapshot['coverage']
        kind = coverage['kind']
        if kind not in {'notion', 'github', 'discord', 'figma', 'analysis'} or len(rows) > 1000:
            raise ValueError('invalid source snapshot')
        coverage = dict(coverage)
        if kind != 'analysis':
            previous = self.coverage().get(kind, {})
            latest = self.db.execute("SELECT max(json_extract(data,'$.observed_at')) FROM documents WHERE kind=?", (kind,)).fetchone()[0]
            coverage['last_success'] = (coverage.get('checked', now())
                if rows or not coverage.get('issues') else previous.get('last_success') or latest)
        with self.db:
            for row in rows:
                if row['kind'] != kind or not self.valid_url(kind, row['url']):
                    raise ValueError('source scope violation')
                raw = json.dumps(row, ensure_ascii=False)
                if len(raw) > 100000:
                    raise ValueError('document too large')
                stable = {k: row.get(k) for k in ('kind', 'id', 'url', 'title', 'content', 'author_id', 'revision')}
                digest = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                self.db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?)',
                                (kind, row['id'], raw, digest))
            self.db.execute('INSERT OR REPLACE INTO collection_status VALUES(?,?)',
                            (kind, json.dumps(coverage, ensure_ascii=False)))

    @staticmethod
    def valid_url(kind, url):
        if kind == 'notion':
            try:
                notion_url(url)
                return True
            except ValueError:
                return False
        p = urlparse(url)
        if kind == 'figma':
            from .figma import FILE_KEY
            return p.scheme == 'https' and p.netloc == 'www.figma.com' and p.path == '/design/' + FILE_KEY
        if kind == 'github':
            return p.scheme == 'https' and p.netloc == 'github.com' and p.path.startswith('/' + GITHUB_ORG + '/')
        parts = p.path.strip('/').split('/')
        return (kind == 'discord' and p.scheme == 'https' and p.netloc == 'discord.com'
                and len(parts) == 4 and parts[:2] == ['channels', '1413450780068937790']
                and all(s.isdigit() for s in parts[2:]))

    def documents(self, limit=150):
        # Round-robin sources so prolific GitHub/Discord traffic cannot starve Notion.
        from itertools import zip_longest
        groups = [[json.loads(r[0]) for r in self.db.execute(
            "SELECT data FROM documents WHERE kind=? ORDER BY json_extract(data,'$.observed_at') DESC,id LIMIT ?",
            (kind, limit))] for kind in ('notion', 'discord', 'github', 'figma')]
        return [row for group in zip_longest(*groups) for row in group if row is not None][:limit]

    def coverage(self):
        return {r[0]: json.loads(r[1]) for r in self.db.execute('SELECT kind,data FROM collection_status')}

    def source_status(self):
        counts = dict(self.db.execute('SELECT kind,count(*) FROM documents GROUP BY kind'))
        coverage = self.coverage()
        review = self.proposal_review()
        return {'workspace': 'gujeuk-server', 'checked': now(), 'notion_writes': False,
                'sources': {kind: {'documents': counts.get(kind, 0),
                    'coverage': coverage.get(kind, {'issues': ['not_collected']})}
                    for kind in ('notion', 'discord', 'github', 'figma')},
                'analysis': coverage.get('analysis'), 'tasks': len(self.items()),
                'pending_proposals': review['counts']['review_groups'],
                'task_count_kind': 'local_drafts_not_official_notion_tasks',
                'proposal_review': {k: v for k, v in review.items() if k != 'groups'}}

    def proposal_created_at(self, row):
        if row.get('created_at'):
            return row['created_at']
        match = re.fullmatch(r'(?:request:ai:|analysis:)([0-9a-f]{64}):[0-9]+', row['source_key'])
        if match:
            run = self.db.execute('SELECT created FROM model_runs WHERE digest=?', (match[1],)).fetchone()
            if run:
                return run[0]  # Same atomic analysis transaction, not a guessed task date.
        return ''

    def backup_to(self, path):
        import os
        if path.is_symlink() or path.exists():
            raise ValueError('backup destination must be new')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        target = sqlite3.connect(path)
        try:
            self.db.backup(target)
            if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('backup verification failed')
        finally:
            target.close()

    def search_documents(self, query='', kind='', limit=10):
        if (not isinstance(query, str) or len(query) > 400
                or kind not in {'', 'notion', 'discord', 'github', 'figma'}
                or type(limit) is not int or not 1 <= limit <= 20):
            raise ValueError('invalid source query')
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        rows = self.db.execute("""SELECT data FROM documents
            WHERE (?='' OR kind=?) AND (json_extract(data,'$.content') LIKE ? ESCAPE '\\'
              OR json_extract(data,'$.title') LIKE ? ESCAPE '\\')
            ORDER BY json_extract(data,'$.observed_at') DESC,id LIMIT ?""",
            (kind, kind, '%' + escaped + '%', '%' + escaped + '%', limit))
        return [json.loads(row[0]) for row in rows]

    def schedule_notion(self, urls):
        with self.db:
            for url in urls:
                self.db.execute('INSERT OR IGNORE INTO notion_frontier(url) VALUES(?)', (notion_url(url),))

    def due_notion(self):
        import time
        cooldown = self.db.execute("SELECT value FROM workspace_meta WHERE key='notion_host_retry'").fetchone()
        if cooldown and float(cooldown[0]) > time.time():
            return None
        row = self.db.execute('SELECT url FROM notion_frontier WHERE next_try<=? ORDER BY checked IS NOT NULL,checked,url LIMIT 1',
            (time.time(),)).fetchone()
        return row[0] if row else None

    def record_notion_read(self, result):
        import math
        import time
        url = notion_url(result['requested_url'])
        docs = result.get('documents', [])
        coverage = result.get('coverage', {'kind': 'notion', 'issues': [result.get('error', 'reader_failed')]})
        if docs:
            self.ingest({'documents': docs, 'coverage': coverage})
        self.schedule_notion([url] + result.get('discovered_urls', []) + [link['url'] for d in docs for link in d.get('links', [])])
        ok = bool(docs) and not any(d.get('content_truncated') for d in docs)
        errors = coverage.get('issues', [])
        reason = 'source_incomplete_or_failed'
        if errors:
            first = errors[0]
            reason = str(first.get('error') or first.get('reason') or 'reader_failed') if isinstance(first, dict) else str(first)
            if isinstance(first, dict) and first.get('status'):
                reason += ':' + str(first['status'])
        elif result.get('error'):
            reason = str(result['error'])
        if not re.fullmatch('[A-Za-z0-9_:-]{1,100}', reason):
            reason = 'reader_failed'
        with self.db:
            blocked = [e for e in errors if isinstance(e, dict) and e.get('status') == 429]
            if blocked:
                retries = []
                for issue in blocked:
                    try:
                        value = float(issue.get('retry_at') or 0)
                        if math.isfinite(value) and value > time.time():
                            retries.append(value)
                    except (TypeError, ValueError, OverflowError):
                        pass
                retry = max(retries, default=time.time() + 900)
                previous = self.db.execute("SELECT value FROM workspace_meta WHERE key='notion_host_retry'").fetchone()
                if previous:
                    try:
                        old = float(previous[0])
                        if math.isfinite(old):
                            retry = max(retry, old)
                    except (TypeError, ValueError, OverflowError):
                        pass
                self.db.execute("INSERT OR REPLACE INTO workspace_meta VALUES('notion_host_retry',?)", (str(retry),))
            self.db.execute('UPDATE notion_frontier SET state=?,next_try=?,checked=?,error=? WHERE url=?',
                ('read' if ok else 'failed', time.time() + (3600 if ok else 900), time.time(),
                 '' if ok else reason, url))
        return {'cached_documents': len(docs), 'frontier': self.notion_frontier_status()}

    def notion_frontier_status(self):
        counts = dict(self.db.execute('SELECT state,count(*) FROM notion_frontier GROUP BY state'))
        failures = dict(self.db.execute("SELECT error,count(*) FROM notion_frontier WHERE state='failed' GROUP BY error"))
        return {'known_pages': sum(counts.values()), 'states': counts, 'failures': failures, 'discoverability_complete': False}

    def seed_notion_cache(self):
        if self.db.execute("SELECT 1 FROM workspace_meta WHERE key='notion_frontier_seeded'").fetchone():
            return
        for row in self.db.execute("SELECT data FROM documents WHERE kind='notion'"):
            data = json.loads(row[0])
            self.schedule_notion([data['url']] + [link['url'] for link in data.get('links', [])])
        with self.db:
            self.db.execute("INSERT INTO workspace_meta VALUES('notion_frontier_seeded','1')")

    def snapshot(self):
        review = self.proposal_review()
        return {'protocol': 1, 'workspace': 'gujeuk-server', 'observed_at': now(),
                'items': [asdict(i) for i in self.items()], 'proposals': review['groups'],
                'proposal_review': {k: v for k, v in review.items() if k != 'groups'},
                'estimates': [e for i in self.items() if (e := self.estimate(i.id))],
                'coverage': self.coverage(), 'documents': self.documents(100),
                'authority': 'notion', 'notion_writes': False}

    def command(self, request):
        """Exactly-once DB mutations on retry: receipt and mutation share a transaction.

        A separate connection observes only the committed transaction. Store's
        nested transaction contexts use savepoints for atomic receipts.
        """
        if not isinstance(request, dict) or set(request) - {'id', 'op', 'data'}:
            raise ValueError('invalid request')
        op, data = request.get('op'), request.get('data', {})
        if op == 'snapshot':
            return self.snapshot()
        if op == 'status':
            return self.source_status()
        if op == 'search':
            if not isinstance(data, dict) or set(data) - {'query', 'kind', 'limit'}:
                raise ValueError('invalid source query')
            return self.search_documents(**data)
        if op == 'record_notion_read':
            return self.record_notion_read(data)
        key = request.get('id', '')
        if not isinstance(key, str) or not 1 <= len(key) <= 200 or not isinstance(data, dict):
            raise ValueError('request id required')
        digest = hashlib.sha256(json.dumps({'op': op, 'data': data}, sort_keys=True).encode()).hexdigest()
        # All public methods below use Store's transaction contexts. This
        # connection wrapper translates nested contexts into SQLite savepoints.
        real = self.db
        self.db = _NestedConnection(real)
        try:
            with self.db:
                row = self.db.execute('SELECT digest,result FROM requests WHERE id=?', (key,)).fetchone()
                if row:
                    if row['digest'] != digest:
                        raise ValueError('request id reused with different data')
                    return json.loads(row['result'])
                if op == 'create':
                    result = {'item_id': self.add(data, source_key='request:' + key)}
                elif op == 'propose':
                    ident, version, patch = data['item_id'], data['version'], data['patch']
                    result = self.propose_change(ident, version, patch,
                        source_key='request:' + key, url=data.get('url', ''))
                elif op == 'resolve':
                    if type(data.get('approve')) is not bool:
                        raise ValueError('approval must be boolean')
                    self.resolve(data['proposal_id'], data['approve'])
                    result = {'resolved': data['proposal_id'], 'approved': data['approve']}
                elif op == 'agent_actions':
                    actions = data.get('actions')
                    if not isinstance(actions, list) or len(actions) > 20:
                        raise ValueError('invalid action list')
                    results = []
                    for index, action in enumerate(actions):
                        if not isinstance(action, dict) or set(action) != {'op', 'data'} or action['op'] not in {'create', 'propose'}:
                            raise ValueError('agent cannot approve or delete')
                        results.append(self.command({'id': f'{key}:{index}', **action}))
                    result = {'results': results}
                else:
                    raise ValueError('unsupported operation')
                self.db.execute('INSERT INTO requests VALUES(?,?,?)', (key, digest, json.dumps(result)))
                return result
        finally:
            self.db = real

    def model_digest(self, documents):
        payload = {'documents': [{k: d.get(k) for k in ('kind', 'id', 'content', 'revision')} for d in documents],
                   'items': [asdict(i) for i in self.items()]}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def analyzed(self, digest):
        return self.db.execute('SELECT 1 FROM model_runs WHERE digest=?', (digest,)).fetchone() is not None

    def apply_analysis(self, digest, output, documents):
        """Model has no arbitrary write API. Validate ALL outputs before atomically applying."""
        if self.analyzed(digest):
            return {'duplicate': True}
        if not isinstance(output, dict) or set(output) - {'actions', 'estimates', 'summary'}:
            raise ValueError('invalid model response')
        actions, estimates = output.get('actions', []), output.get('estimates', [])
        if not isinstance(actions, list) or not isinstance(estimates, list) or len(actions) > 30 or len(estimates) > 30:
            raise ValueError('analysis bounds')
        evidence = {d['url']: d for d in documents}
        real = self.db
        self.db = _NestedConnection(real)
        try:
            with self.db:
                if self.analyzed(digest):
                    return {'duplicate': True}
                for index, action in enumerate(actions):
                    url = action['url']
                    if url not in evidence or evidence[url]['kind'] not in {'notion', 'discord'}:
                        raise ValueError('missing primary source evidence')
                    data = action['data']
                    if 'item_id' in action:
                        self.propose_change(action['item_id'], action['version'], data,
                            source_key=f'analysis:{digest}:{index}', url=url, automated=True)
                    else:
                        stable = hashlib.sha256((url + '\n' + data['title'].strip()).encode()).hexdigest()
                        self.add(data, source='local', source_key=f'ai-source:{stable}', url=url)
                for estimate in estimates:
                    urls = estimate['evidence']
                    if not urls or any(u not in evidence or evidence[u]['kind'] != 'github' for u in urls):
                        raise ValueError('estimate needs inspected GitHub evidence')
                    revisions = {evidence[u].get('revision') for u in urls if evidence[u].get('revision')}
                    if estimate['revision'] not in revisions:
                        raise ValueError('estimate revision not inspected')
                    self.record_estimate(estimate['item_id'], version=estimate['version'], percent=estimate['percent'],
                        reason=estimate['reason'], evidence=urls, revision=estimate['revision'], analyzed_at=now())
                self.db.execute('INSERT INTO model_runs VALUES(?,?)', (digest, now()))
                self.db.execute('INSERT OR REPLACE INTO collection_status VALUES(?,?)', ('analysis', json.dumps({
                    'checked': now(), 'digest': digest, 'summary': str(output.get('summary', ''))[:16000],
                    'actions': len(actions), 'estimates': len(estimates)})))
            return {'actions': len(actions), 'estimates': len(estimates)}
        finally:
            self.db = real


class _NestedConnection:
    """SAVEPOINT contexts preserve the outer request/analysis transaction."""
    def __init__(self, db):
        self.db, self.depth = db, 0
        self.owns_transaction = False

    def __getattr__(self, key):
        return getattr(self.db, key)

    def __enter__(self):
        if self.depth == 0 and not self.db.in_transaction:
            self.db.execute('BEGIN IMMEDIATE')
            self.owns_transaction = True
        self.depth += 1
        self.db.execute(f'SAVEPOINT gujeuk_{self.depth}')
        return self

    def __exit__(self, kind, value, tb):
        name = f'gujeuk_{self.depth}'
        if kind:
            self.db.execute(f'ROLLBACK TO {name}')
        self.db.execute(f'RELEASE {name}')
        self.depth -= 1
        if self.depth == 0 and self.owns_transaction:
            self.db.rollback() if kind else self.db.commit()
            self.owns_transaction = False
