"""Authenticated SSH RPC and read-through offline cache; never a second writer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid
from .agent import remote_command
from .db import Item, default_db_path

MAX_REPLY = 12 * 1024 * 1024


def rpc_command():
    command = remote_command()
    directory = os.environ.get('GUJEUK_AI_REMOTE_DIR', '/home/ubuntu/gujeuk-pm')
    # PM sidecar, never run arbitrary supplied shell commands or publish a port.
    command[-1] = (f'cd {shlex.quote(directory)} && exec docker compose -f compose.yaml '
                   '-f compose.pm.yaml exec -T pm-worker python -m oijwef098234.gujeuk_ai.runtime '
                   'rpc --state /state')
    return command


def ssh_request(request):
    try:
        with subprocess.Popen(rpc_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as process:
            try:
                # communicate also handles EOF and timeout teardown; bounded server
                # snapshot plus explicit local size guard before JSON decode.
                raw, _ = process.communicate(json.dumps(request).encode(), timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise ValueError('서버 응답이 늦어요. 변경 결과는 다시 연결해 확인해 주세요') from None
            if len(raw) > MAX_REPLY:
                raise ValueError('서버 응답 크기가 너무 커요')
            try:
                envelope = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise ValueError('구즉 서버에 연결하지 못했어요. SSH와 PM 서비스 상태를 확인해 주세요') from None
            if not envelope.get('ok'):
                raise ValueError('서버가 요청을 거절했어요. 최신 버전과 승인 상태를 확인해 주세요')
            if process.returncode:
                raise ValueError('서버 처리 결과를 확인하지 못했어요. 새로고침해 주세요')
            return envelope['result']
    except OSError:
        raise ValueError('SSH를 실행하지 못했어요') from None


class RemoteStore:
    """UI projection of one server workspace. All mutations go to server.

    Offline: cached view remains available, writes fail; no local queued edits.
    Cache is separate from the user's existing local draft SQLite DB.
    """
    remote = True

    def __init__(self, path=None, request=ssh_request):
        self.path = Path(path) if path else default_db_path().with_name('server-cache.json')
        self.request = request
        self.online = False
        self.error = ''
        self.data = {'items': [], 'proposals': [], 'estimates': [], 'documents': [], 'coverage': {}}
        self.identity = rpc_command()
        if self.path.is_symlink():
            raise ValueError('캐시 심볼릭 링크는 사용할 수 없어요')
        if self.path.exists() and self.path.stat().st_size <= MAX_REPLY:
            try:
                cached = json.loads(self.path.read_text())
                if cached.get('identity') == self.identity:
                    self._validate_snapshot(cached['snapshot'])
                    self.data = cached['snapshot']
            except (ValueError, TypeError, KeyError):
                self.error = '서버 캐시 형식을 확인해 주세요'

    @staticmethod
    def _validate_snapshot(data):
        if (data.get('protocol') != 1 or data.get('workspace') != 'gujeuk-server'
                or not isinstance(data.get('items'), list) or len(data['items']) > 20000):
            raise ValueError('서버 스냅샷 형식이 달라요')
        for row in data['items']:
            Item(**row)

    def refresh(self):
        try:
            data = self.request({'op': 'snapshot'})
            self._validate_snapshot(data)
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.path.with_suffix('.tmp-' + uuid.uuid4().hex)
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w') as stream:
                    json.dump({'identity': self.identity, 'snapshot': data}, stream, ensure_ascii=False)
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self.data, self.online, self.error = data, True, ''
        except (ValueError, OSError) as error:
            self.online = False
            self.error = str(error) if isinstance(error, ValueError) else '캐시를 저장하지 못했어요'
            raise ValueError(self.error) from None

    def _write(self, op, data):
        # No optimistic cache mutation, so a failed/ambiguous request cannot
        # masquerade as confirmed state. request IDs make transport retries safe.
        request = {'id': str(uuid.uuid4()), 'op': op, 'data': data}
        try:
            return self.request(request)
        except ValueError:
            # Same immutable request ID if the first reply was lost after commit.
            return self.request(request)

    def add(self, data):
        return self._write('create', data)['item_id']

    def update(self, ident, data, expected):
        return self._write('propose', {'item_id': ident, 'version': expected, 'patch': data})

    def resolve(self, ident, approve):
        return self._write('resolve', {'proposal_id': ident, 'approve': approve})

    def apply_reply(self, text):
        import re
        blocks = re.findall(r'```gujeuk-actions\s*\n(.*?)```', text, re.DOTALL)
        if not blocks:
            return None
        if len(blocks) != 1 or len(blocks[0]) > 100000:
            raise ValueError('AI 변경 제안 형식을 확인해 주세요')
        return self._write('agent_actions', {'actions': json.loads(blocks[0])})

    def items(self, kind='', owner=''):
        return [i for row in self.data['items'] if (i := Item(**row)) and
                (not kind or i.kind == kind) and (not owner or owner in i.assignees)]

    def get(self, ident):
        for item in self.items():
            if item.id == ident:
                return item
        raise ValueError('항목을 찾을 수 없어요')

    def proposals(self):
        return self.data['proposals']

    def estimate(self, ident):
        return next((e for e in self.data['estimates'] if e['item_id'] == ident), None)

    def calendar_items(self, first, last):
        return [i for i in self.items() if i.due and (i.begins or i.due) <= last.isoformat() and i.due >= first.isoformat()]

    def status(self):
        return {'remote': True, 'online': self.online, 'observed_at': self.data.get('observed_at'),
                'authority': 'notion', 'notion_writes': False, 'coverage': self.data['coverage'],
                'pending': len(self.proposals()), 'drafts': len(self.items()), 'error': self.error}

    def source_context(self):
        return {'coverage': self.data['coverage'], 'documents': [d | {'content': d['content'][:2000]}
                for d in self.data.get('documents', [])[:60]],
                'limits': '최근 원문 60개, 본문 각 2000자까지. 전체 자료가 아니에요.',
                'observed_at': self.data.get('observed_at'), 'online': self.online}

    def close(self):
        pass
