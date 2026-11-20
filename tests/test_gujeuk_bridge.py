import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from oijwef098234.gujeuk_ai.bridge import invoke
from oijwef098234.gujeuk_ai.sources import document
from oijwef098234.gujeuk_ai.workspace import Workspace


def test_workspace_source_status_and_literal_search(tmp_path):
    store=Workspace(tmp_path/'workspace.db')
    url='https://github.com/GuJeuk-Check-in/repo/pull/1'
    store.ingest({'documents':[document('github','1',url,'100% 구현','실제 PR 근거')],
                  'coverage':{'kind':'github','checked':'2026-11-19','issues':[]}})
    result=store.command({'op':'status'})
    assert isinstance(result, dict)
    assert result['sources']['github']['documents']==1
    assert result['sources']['discord']['coverage']['issues']==['not_collected']
    assert len(store.command({'op':'search','data':{'query':'100%'}}))==1
    assert store.command({'op':'search','data':{'query':'_'}})==[]
    with pytest.raises(ValueError):
        store.command({'op':'search','data':{'kind':'private'}})
    store.close()


def test_bridge_limits_operations_and_preserves_request_id():
    calls=[]
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'result':{'proposal_id':9}}))
    result=invoke({'op':'propose','request_id':'retry-1',
                   'data':{'item_id':1,'version':1,'patch':{'status':'doing'}}},runner=runner)
    assert result['result']['proposal_id']==9
    payload=json.loads(calls[0][1]['input'])
    assert payload['id']=='bot:retry-1' and payload['op']=='propose'
    assert set(calls[0][1]['env'])=={'PATH','PYTHONPATH','PYTHONDONTWRITEBYTECODE'}
    for op in ('resolve','delete','shell','snapshot'):
        with pytest.raises(ValueError):
            invoke({'op':op},runner=runner)
    with pytest.raises(ValueError):
        invoke({'op':'create','data':{'title':'no ID'}},runner=runner)
    assert len(calls)==1


def test_bridge_bounded_evidence_and_sanitized_transport():
    def runner(command,**kwargs):
        assert json.loads(kwargs['input'])['data']['limit']==10
        return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'result':[{'content':'x'*9000}]}))
    result=invoke({'op':'search','kind':'notion'},runner=runner)
    assert len(result['result']['documents'][0]['content'])==6000
    with pytest.raises(ValueError,match='workspace request failed'):
        invoke({'op':'status'},runner=lambda *a,**kw:SimpleNamespace(returncode=1,stdout='sensitive-invalid-output'))


def test_plugin_registers_narrow_schema():
    path=Path(__file__).parents[1]/'services/gujeuk_pm/plugin/__init__.py'
    spec=importlib.util.spec_from_file_location('gujeuk_pm_test_plugin',path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registrations=[]
    module.register(SimpleNamespace(register_tool=lambda **kwargs:registrations.append(kwargs)))
    assert len(registrations)==1
    schema=registrations[0]['schema']
    assert schema['name']=='gujeuk_pm'
    assert 'resolve' not in schema['parameters']['properties']['op']['enum']


def test_default_cli_uses_server_without_migrating_local_data(monkeypatch):
    from oijwef098234.gujeuk_ai import _remote_mode
    monkeypatch.delenv('GUJEUK_AI_REMOTE', raising=False)
    args=SimpleNamespace(local=False,remote=False,import_discord=None)
    assert _remote_mode(args)
    args.local=True
    assert not _remote_mode(args)
    args.local=False
    monkeypatch.setenv('GUJEUK_AI_REMOTE','0')
    assert not _remote_mode(args)
    args.remote=True
    assert _remote_mode(args)


def test_verified_backups_preserve_tasks_receipts_and_source_state(tmp_path):
    from oijwef098234.gujeuk_ai.ops import backup
    store=Workspace(tmp_path/'workspace.db')
    discord_store=Workspace(tmp_path/'fake-discord.db')
    request={'id':'once','op':'create','data':{'title':'실제 테스트 전용'}}
    result=store.command(request)
    info=backup(store,discord_store,tmp_path/'backups')
    destination=tmp_path/'backups'/info['backup']/'workspace.db'
    assert destination.stat().st_mode & 0o777==0o600
    restored=Workspace(destination)
    assert restored.command(request)==result
    assert len(restored.items())==1
    with pytest.raises(ValueError):
        store.backup_to(destination)
    store.close(); discord_store.close(); restored.close()


def test_health_distinguishes_partial_sources_from_stopped_collection():
    from datetime import datetime, timezone
    from oijwef098234.gujeuk_ai.ops import health
    checked=10000
    stamp=datetime.fromtimestamp(checked,timezone.utc).isoformat()
    status={'sources':{kind:{'documents':1,'coverage':{'checked':stamp,'issues':['partial']}}
                       for kind in ('notion','discord','github')}}
    assert health(status,{'finished':checked},checked=checked)['healthy']
    stale=health(status,{'finished':checked},checked=checked+4000)
    assert not stale['healthy']
    assert 'notion:stale' in stale['issues']
    assert 'discord:collector_stale' in stale['issues']


def test_github_rotates_bounded_repositories():
    from oijwef098234.gujeuk_ai.sources import collect_github
    calls=[]
    def get(path):
        calls.append(path)
        return [{'name':name} for name in ('one','two','three','four')] if path.startswith('/orgs/') else []
    result=collect_github(get=get,start=3,max_repos=2)
    assert any('/four/issues' in path for path in calls)
    assert any('/one/issues' in path for path in calls)
    assert not any('/two/' in path for path in calls)
    assert result['coverage']['next_start']==5


def test_github_rate_limit_backoff_never_echoes_raw_response(monkeypatch):
    from email.message import Message
    from urllib.error import HTTPError
    from oijwef098234.gujeuk_ai import sources
    headers=Message(); headers['X-RateLimit-Remaining']='0'; headers['X-RateLimit-Reset']='200'
    calls=[]
    def opener(*args,**kwargs):
        calls.append(True)
        raise HTTPError('https://api.github.com',403,'sensitive-body',headers,None)
    monkeypatch.setattr(sources,'urlopen',opener)
    monkeypatch.setattr(sources.time,'time',lambda:100)
    monkeypatch.setattr(sources,'_github_reset',0)
    for _ in range(2):
        with pytest.raises(sources.SourceHTTPError) as error:
            sources.github_get('/orgs/GuJeuk-Check-in/repos')
        assert error.value.retry_at==200
        assert 'sensitive' not in str(error.value)
    assert len(calls)==1
