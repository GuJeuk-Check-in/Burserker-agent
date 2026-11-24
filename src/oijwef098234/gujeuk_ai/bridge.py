"""Narrow model-facing bridge. No approval/delete/SQL/shell operation is exposed."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
from .reporting import citation_sources


def invoke(params, *, runner=subprocess.run):
    if not isinstance(params, dict) or set(params) - {'op', 'query', 'kind', 'provider', 'request_id', 'data', 'url'}:
        raise ValueError('invalid PM tool arguments')
    op = params.get('op', 'status')
    if op in {'read_public_url', 'search_public_web'}:
        from .public_research import read_public_url, search_public_web
        if op == 'read_public_url':
            if set(params) != {'op', 'url'}:
                raise ValueError('invalid_public_read_arguments')
            result = read_public_url(params['url'])
        else:
            if set(params) != {'op', 'query'}:
                raise ValueError('invalid_public_search_arguments')
            result = search_public_web(params['query'])
        return {'ok': True, 'result': result, 'untrusted_source_content': True}
    if op in {'public_menu', 'meal_suggest'}:
        from .member_guidance import public_menu
        result = public_menu(params.get('provider', 'all'), params.get('query', ''))
        if op == 'meal_suggest':
            result['suggestions'] = []
            for index in range(2):
                for page in result['pages']:
                    if len(page['items']) > index:
                        item = page['items'][index]
                        result['suggestions'].append({
                            'menu': item['menu'], 'price_krw': item['price_krw'],
                            'price_evidence': item['price_evidence'], 'source_url': page['url'],
                            'provider': page['name']})
            result['limits'] += ' 추천은 확인된 메뉴 후보이며 팀 선호·예산·재고를 추정하지 않아요.'
        return {'ok': True, 'result': result, 'untrusted_source_content': True}
    if op == 'minutes_draft':
        query = params.get('query', '회의')
        kind = params.get('kind', 'discord')
        if not isinstance(query, str) or not query.strip() or len(query) > 80 or kind not in {'discord', 'notion'}:
            raise ValueError('invalid_minutes_query')
        docs = _rpc({'op': 'search', 'data': {'query': query.strip(), 'kind': kind, 'limit': 10}}, runner)
        data = {'documents': [row | {'content': row.get('content', '')[:6000]} for row in docs],
                'limits': '회의록 초안 후보 원문이에요. 접근 권한을 걸러낸 뒤 명시적 결정·제안만 초안에 넣어요.'}
        return {'ok': True, 'result': data, 'untrusted_source_content': True}
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
