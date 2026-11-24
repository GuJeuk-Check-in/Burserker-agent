"""Narrow model-facing bridge. No approval/delete/SQL/shell operation is exposed."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
from .reporting import citation_sources


def invoke(params, *, runner=subprocess.run):
    if not isinstance(params, dict) or set(params) - {'op', 'query', 'kind', 'request_id', 'data', 'url'}:
        raise ValueError('invalid PM tool arguments')
    op = params.get('op', 'status')
    if op in {'prepare_schedule', 'activate_schedule', 'schedules', 'cancel_schedule'}:
        from .schedules import invoke as schedule
        return schedule(params)
    if op in {'channels', 'prepare_message', 'send_message'}:
        from .messaging import invoke as message
        return message(params)
    if op == 'read_notion':
        from .notion_reader import request_page
        from .sources import NOTION_ROOT
        result = request_page('/opt/data/pm-workspace', params.get('url') or NOTION_ROOT)
        if result.get('documents'):
            try:
                result['cache'] = _rpc({'op': 'record_notion_read', 'data': result}, runner)
            except ValueError:
                result['cache_error'] = 'cache_not_updated'
        result.pop('discovered_urls', None)
        result['citation_sources'] = citation_sources(result.get('documents', []))
        return {'ok': True, 'result': result, 'untrusted_source_content': True}
    if op == 'tasks':
        request = {'op': 'snapshot'}
    elif op == 'status':
        request = {'op': 'status'}
    elif op == 'search':
        request = {'op': 'search', 'data': {'query': params.get('query', ''),
                    'kind': params.get('kind', ''), 'limit': 10}}
    elif op == 'wiki_search':
        request = {'op': op, 'data': {'query': params.get('query', ''), 'limit': 10}}
    elif op == 'wiki_status':
        request = {'op': op}
    elif op in {'create', 'propose'}:
        if not isinstance(params.get('request_id'), str) or not params['request_id']:
            raise ValueError('request_id required for mutations')
        request = {'id': 'bot:' + params['request_id'], 'op': op, 'data': params.get('data', {})}
    else:
        raise ValueError('agent may not approve, delete, or execute commands')
    data = _rpc(request, runner)
    if op == 'status' and isinstance(data.get('analysis'), dict):
        data['analysis'].pop('summary', None)  # Summaries may combine private-channel evidence.
    if op == 'tasks':
        items = [row | {'note': row.get('note', '')[:1500]} for row in data['items'][:200]]
        data = {'items': items, 'pending_proposals': data['proposals'][:100],
                'proposal_review': data.get('proposal_review', {}),
                'observed_at': data['observed_at'], 'notion_writes': False,
                'limits': '서버 초안이에요. Notion 공식 상태와 구분하고 원문을 확인하세요.'}
    elif op in {'search', 'wiki_search'}:
        data = {'documents': [row | {'content': row['content'][:6000]} for row in data],
                'limits': '검색 최대 10개, 본문 각 6000자. 결과 없음은 전체 자료 없음이 아니에요.'}
        data['citation_sources'] = citation_sources(data['documents'])
    return {'ok': True, 'result': data, 'untrusted_source_content': True}


def _rpc(request, runner):
    root = Path('/opt/data/workspace/gujeuk-pm/src')
    env = {'PATH': os.defpath, 'PYTHONPATH': str(root), 'PYTHONDONTWRITEBYTECODE': '1'}
    options = {'user': 1000, 'group': 1000, 'extra_groups': []} if os.geteuid() == 0 else {}
    result = runner([sys.executable, '-B', '-m', 'oijwef098234.gujeuk_ai.runtime',
                     'rpc', '--state', '/opt/data/pm-workspace'],
        input=json.dumps(request), text=True, capture_output=True, timeout=30, env=env, **options)
    if result.returncode or len(result.stdout) > 12 * 1024 * 1024:
        raise ValueError('PM workspace request failed; inspect connection and source status')
    envelope = json.loads(result.stdout)
    if not envelope.get('ok'):
        raise ValueError('PM request rejected; refresh item version and approval state')
    return envelope['result']
