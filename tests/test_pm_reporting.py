import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from oijwef098234.gujeuk_ai.access import filter_evidence, GUILD_ID
from oijwef098234.gujeuk_ai.bridge import invoke
from oijwef098234.gujeuk_ai.reporting import REPLY_POLICY, citation_sources
from oijwef098234.gujeuk_ai.sources import NOTION_ROOT


def test_policy_reaches_actual_registered_tool():
    path = Path(__file__).resolve().parents[1] / 'services/gujeuk_pm/plugin/__init__.py'
    spec = importlib.util.spec_from_file_location('pm_reporting_plugin', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tools = []
    module.register(SimpleNamespace(register_tool=lambda **kwargs: tools.append(kwargs)))
    assert REPLY_POLICY in tools[0]['schema']['description']
    assert '현재 상태 → 주요 제약 → 다음 확인' in REPLY_POLICY
    assert '날짜·일정 각각' in REPLY_POLICY and '미완료 업무 수가 아니에요' in REPLY_POLICY


def test_search_keeps_actual_calendar_urls_in_citation_pointers():
    docs = [{'kind': 'notion', 'id': 'calendar', 'title': '개발 일정',
             'url': NOTION_ROOT, 'content': '11/2 개발 일정'}]
    def run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'result': docs}))
    data = invoke({'op': 'search', 'kind': 'notion', 'query': '개발'}, runner=run)['result']
    assert data['citation_sources'] == [{'id': 'calendar', 'kind': 'notion',
        'title': '개발 일정', 'url': NOTION_ROOT}]
    assert citation_sources([{'content': '11/2 출처 없음'}]) == []


def test_permission_filter_removes_private_proposals_and_citation_sidecar():
    private = {'kind': 'discord', 'url': f'https://discord.com/channels/{GUILD_ID}/555/777',
               'content': '비공개 근거'}
    public = {'kind': 'notion', 'url': NOTION_ROOT, 'content': '공개 근거'}
    data = {'result': {'documents': [private, public],
            'citation_sources': citation_sources([private, public]),
            'items': [{'id': 1, 'url': private['url']}, {'id': 2, 'url': NOTION_ROOT}],
            'pending_proposals': [{'id': 3, 'url': private['url']}, {'id': 4, 'url': NOTION_ROOT}]}}
    def denied(path):
        raise PermissionError('denied')
    result = filter_evidence(data, frozenset(), '42', get=denied)['result']
    assert result['documents'] == [public]
    assert result['citation_sources'] == citation_sources([public])
    assert [row['id'] for row in result['items']] == [2]
    assert [row['id'] for row in result['pending_proposals']] == [4]


def test_tasks_projection_keeps_queue_semantics():
    snapshot = {'items': [], 'proposals': [{'id': 1}], 'observed_at': 'now',
                'proposal_review': {'counts': {'pending_records': 543, 'review_groups': 1}}}
    def run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'result': snapshot}))
    result = invoke({'op': 'tasks'}, runner=run)['result']
    assert result['proposal_review']['counts']['pending_records'] == 543
    assert result['pending_proposals'] == [{'id': 1}]
    assert '공식 상태와 구분' in result['limits']
