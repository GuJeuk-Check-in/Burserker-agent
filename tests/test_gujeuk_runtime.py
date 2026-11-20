import json
import pytest

from oijwef098234.gujeuk_ai.sources import NOTION_ROOT, collect_notion, collect_github, document, notion_url, safe_path
from oijwef098234.gujeuk_ai.workspace import Workspace
from oijwef098234.gujeuk_ai.sync import RemoteStore
from oijwef098234.gujeuk_ai.runtime import tick, analyze


def req(op, data=None, key='key'):
    return {'id': key, 'op': op, 'data': data or {}}


@pytest.fixture
def store(tmp_path):
    value = Workspace(tmp_path / 'workspace.db')
    yield value
    value.close()


def test_public_scope_and_shell():
    assert notion_url(NOTION_ROOT + '?x=1') == NOTION_ROOT
    with pytest.raises(ValueError):
        notion_url('https://evil.test/' + 'a' * 32)
    result = collect_notion(reader=lambda _: ('Notion', 'JavaScript must be enabled', []))
    assert not result['documents'] and result['coverage']['issues']
    assert not safe_path('config/.env.production')
    assert not safe_path('credentials/a.csv')
    assert safe_path('src/main.py')


def test_bounded_notion_crawl_and_source_links():
    child = NOTION_ROOT.rsplit('/', 1)[0] + '/' + 'a' * 32
    def reader(url):
        return '업무 페이지', '실제로 읽은 업무 본문이에요. ' * 10, [child, NOTION_ROOT, 'https://evil.test/abc']
    result = collect_notion(max_pages=1, reader=reader)
    assert len(result['documents']) == 1
    assert result['coverage']['remaining_links'] == 1
    assert result['documents'][0]['url'] == NOTION_ROOT
    assert len(collect_notion(max_pages=3, reader=reader)['documents']) == 2


def test_github_excludes_secret_files():
    def get(path):
        if path.startswith('/orgs/'):
            return [{'name': 'app', 'private': False}]
        if '/issues?' in path:
            return []
        if '/files?' in path:
            return [{'filename': '.env', 'patch': 'SECRET'}, {'filename': 'src/a.py', 'patch': '+pass'}]
        row = {'number': 1, 'html_url': 'https://github.com/GuJeuk-Check-in/app/pull/1', 'title': 'PR', 'head': {'sha': 'abc'}}
        return [row] if '/pulls?' in path else row
    result = collect_github(get=get)
    assert 'SECRET' not in json.dumps(result)
    assert result['documents'][0]['revision'] == 'abc'


def test_rpc_receipt_restart_and_approval(store):
    create = req('create', {'title': '업무'})
    ident = store.command(create)['item_id']
    assert store.command(create)['item_id'] == ident
    assert len(store.items()) == 1
    with pytest.raises(ValueError):
        store.command(req('create', {'title': '다른 업무'}))
    proposal = store.command(req('propose', {'item_id': ident, 'version': 1, 'patch': {'status': 'doing'}}, 'change'))['proposal_id']
    assert store.get(ident).status == 'blocked'
    approve = req('resolve', {'proposal_id': proposal, 'approve': True}, 'approve')
    store.command(approve)
    assert store.get(ident).status == 'doing' and store.get(ident).version == 2
    reopened = Workspace(store.path)
    try:
        assert reopened.command(approve)['approved']
        assert reopened.get(ident).version == 2
        assert len(reopened.items()) == 1
    finally:
        reopened.close()


def test_atomic_agent_batch_rejects_self_approval(store):
    with pytest.raises(ValueError):
        store.command(req('agent_actions', {'actions': [
            {'op': 'create', 'data': {'title': 'must roll back'}},
            {'op': 'resolve', 'data': {'proposal_id': 1, 'approve': True}}]}))
    assert store.items() == []
    assert store.db.execute('SELECT count(*) FROM requests').fetchone()[0] == 0


def test_stale_approval_does_not_overwrite(store):
    ident = store.add({'title': '업무'})
    pid = store.command(req('propose', {'item_id': ident, 'version': 1, 'patch': {'status': 'done'}}))['proposal_id']
    store.update(ident, {'title': '새 업무'}, 1)
    with pytest.raises(ValueError):
        store.command(req('resolve', {'proposal_id': pid, 'approve': True}, 'approve'))
    assert store.get(ident).title == '새 업무'
    assert len(store.proposals()) == 1


def test_atomic_analysis_and_provenance(store):
    doc = document('notion', 'a', NOTION_ROOT, '원문', '업무 요청')
    docs = [doc]
    output = {'actions': [{'url': NOTION_ROOT, 'data': {'title': '업무'}}], 'estimates': []}
    store.apply_analysis('run1', output, docs)
    store.apply_analysis('run2', output, docs)
    assert len(store.items()) == 1
    assert store.apply_analysis('run2', output, docs) == {'duplicate': True}
    bad = {'actions': [{'url': NOTION_ROOT, 'data': {'title': '롤백'}}],
           'estimates': [{'item_id': 1, 'version': 1, 'percent': 100, 'reason': 'invented',
                          'evidence': ['https://github.com/evil/repo'], 'revision': 'fake'}]}
    with pytest.raises(ValueError):
        store.apply_analysis('bad', bad, docs)
    assert len(store.items()) == 1 and not store.analyzed('bad')
    assert not store.snapshot()['notion_writes']


def test_analysis_existing_fields_remain_pending(store):
    ident = store.add({'title': '기존'})
    docs = [document('notion', 'a', NOTION_ROOT, '원문', '상태 변경')]
    store.apply_analysis('update', {'actions': [{'url': NOTION_ROOT, 'item_id': ident,
        'version': 1, 'data': {'status': 'done'}}]}, docs)
    assert store.get(ident).status == 'blocked' and len(store.proposals()) == 1


def test_source_cache_isolated_and_failure_visible(store):
    doc = document('notion', 'a', NOTION_ROOT, '원문', '실제 본문')
    store.ingest({'documents': [doc], 'coverage': {'kind': 'notion'}})
    def fail():
        raise RuntimeError('sensitive exception')
    tick(store, notion=fail, github=lambda: {'documents': [], 'coverage': {'kind': 'github'}})
    assert len(store.documents()) == 1
    assert store.coverage()['notion']['issues'] == ['RuntimeError']
    assert 'sensitive' not in json.dumps(store.coverage())
    with pytest.raises(ValueError):
        store.ingest({'documents': [doc | {'url': 'https://evil.test'}], 'coverage': {'kind': 'notion'}})


def test_fake_model_pipeline_dedup(store):
    docs = [document('notion', 'a', NOTION_ROOT, '업무', '명시된 업무 요청')]
    store.ingest({'documents': docs, 'coverage': {'kind': 'notion'}})
    class Engine:
        def ask(self, prompt, callback):
            assert '원문' in prompt
            assert callback('permission', {}) is None
            return json.dumps({'summary': '확인했어요', 'actions': [{'url': NOTION_ROOT, 'data': {'title': '업무'}}]})
    analyze(store, Engine())
    analyze(store, Engine())
    assert len(store.items()) == 1
    assert analyze(store, Engine()) == {'skipped': 'unchanged'}


def test_remote_cache_and_offline_no_local_write(tmp_path, store):
    remote = RemoteStore(tmp_path / 'server-cache.json', request=store.command)
    remote.refresh()
    remote.add({'title': '서버 항목'})
    remote.refresh()
    assert remote.items()[0].title == '서버 항목'
    assert remote.path.stat().st_mode & 0o777 == 0o600
    remote.update(1, {'status': 'done'}, 1)
    remote.refresh()
    assert remote.get(1).status == 'blocked' and len(remote.proposals()) == 1
    def fail(_):
        raise ValueError('연결 실패')
    offline = RemoteStore(remote.path, request=fail)
    with pytest.raises(ValueError):
        offline.refresh()
    assert offline.items()[0].title == '서버 항목' and not offline.online
    assert store.get(1).status == 'blocked'


def test_chat_actions_are_safe_batch(tmp_path, store):
    remote = RemoteStore(tmp_path / 'cache.json', request=store.command)
    remote.apply_reply('```gujeuk-actions\n[{"op":"create","data":{"title":"질문에서 요청한 업무"}}]\n```')
    assert len(store.items()) == 1
    with pytest.raises(ValueError):
        remote.apply_reply('```gujeuk-actions\n[{"op":"resolve","data":{"proposal_id":1,"approve":true}}]\n```')


# The personal-app Textual integration test remains in its original repository.
# This repository deliberately ships only the server and portable SSH client.


def test_lost_reply_retries_same_receipt(tmp_path, store):
    first = True
    def request(data):
        nonlocal first
        result = store.command(data)
        if first:
            first = False
            raise ValueError('connection lost after commit')
        return result
    remote = RemoteStore(tmp_path / 'cache.json', request=request)
    remote.add({'title': '중복 금지'})
    assert len(store.items()) == 1


def test_balanced_sources_and_secret_screen(store):
    store.ingest({'documents': [document('notion', 'root', NOTION_ROOT, '노션', '업무')],
                  'coverage': {'kind': 'notion'}})
    store.ingest({'documents': [document('github', str(i), f'https://github.com/GuJeuk-Check-in/app/issues/{i}',
                  'PR', '변경') for i in range(200)], 'coverage': {'kind': 'github'}})
    assert store.documents(5)[0]['kind'] == 'notion'
    with pytest.raises(ValueError):
        document('notion', 'secret', NOTION_ROOT, 'excluded', '-----BEGIN PRIVATE KEY-----')


def test_discord_watermark_and_readonly_restart(tmp_path):
    import importlib.util
    from pathlib import Path
    from oijwef098234.gujeuk_ai.runtime import discord_snapshot
    path = Path(__file__).parents[1] / 'services/gujeuk_pm/collector.py'
    spec = importlib.util.spec_from_file_location('collector_for_runtime_test', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = tmp_path / 'discord'
    source = module.Store(state, '1413450780068937790')
    source.discover({'id': '1', 'type': 0})
    source.save_page('1', [{'id': str(i), 'content': 'message'} for i in range(1, 1004)])
    source.set_archive_cursor('/route', 'cursor')
    source.db.close()
    first = discord_snapshot(path, state)
    assert len(first['documents']) == 1000 and first['coverage']['pending_possible']
    second = discord_snapshot(path, state, first['coverage']['cursor'])
    assert len(second['documents']) == 3
    third = discord_snapshot(path, state, second['coverage']['cursor'])
    assert not third['documents']
    reopened = module.Store(state, '1413450780068937790', read_only=True)
    try:
        assert reopened.archive_cursor('/route') == 'cursor'
    finally:
        reopened.db.close()
