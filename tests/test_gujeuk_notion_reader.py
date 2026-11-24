import json
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
import time

import pytest

from oijwef098234.gujeuk_ai import notion_reader as reader
from oijwef098234.gujeuk_ai import sources
from oijwef098234.gujeuk_ai.access import authorize, GUILD_ID
from oijwef098234.gujeuk_ai.bridge import invoke
from oijwef098234.gujeuk_ai.workspace import Workspace


def test_reader_keeps_selected_database_page_and_rejects_offsite():
    url=sources.NOTION_ROOT+'?v='+'a'*32+'&p='+'b'*32+'&pm=s'
    assert sources.notion_url(url)==sources.NOTION_ROOT+'?v='+'a'*32+'&p='+'b'*32+'&pm=s'
    assert sources.page_id(sources.notion_url(url))=='b'*32
    for bad in ('http://127.0.0.1/'+'a'*32,
                'https://magical-sovereign-7b8.notion.site.evil.test/'+'a'*32,
                'https://magical-sovereign-7b8.notion.site@evil.test/'+'a'*32,
                'file:///tmp/'+'a'*32, sources.NOTION_ROOT+'?p=not-a-page',
                sources.NOTION_ROOT+'?v=not-a-view',
                sources.NOTION_ROOT+'?v='+'a'*32+'&v='+'b'*32):
        with pytest.raises(ValueError):reader.read_page(bad)


def test_view_and_page_ids_normalize_without_merging_views():
    dashed='AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'
    assert sources.notion_url(sources.NOTION_ROOT+'?v='+dashed+'&p='+dashed+'&pm=other') == (
        sources.NOTION_ROOT+'?v='+dashed.replace('-','').lower()+'&p='+dashed.replace('-','').lower()+'&pm=s')
    assert sources.notion_url(sources.NOTION_ROOT+'?v='+'a'*32) != sources.notion_url(
        sources.NOTION_ROOT+'?v='+'b'*32)
    assert sources.page_id(sources.NOTION_ROOT.rsplit('/',1)[0]+'/API-'+dashed) == dashed.replace('-','').lower()
    def page(_):
        return '자료실', '동일한 페이지의 다른 공개 보기 본문입니다. '*5, []
    first=sources.collect_notion(sources.NOTION_ROOT+'?v='+'a'*32,max_pages=1,reader=page)
    second=sources.collect_notion(sources.NOTION_ROOT+'?v='+'b'*32,max_pages=1,reader=page)
    assert first['documents'][0]['id'] != second['documents'][0]['id']


def test_notion_retry_after_seconds_and_http_date():
    assert sources.notion_retry_at('17',clock=lambda:1000)==1017
    header=format_datetime(datetime.fromtimestamp(1100,tz=timezone.utc),usegmt=True)
    assert sources.notion_retry_at(header,clock=lambda:1000)==1100
    assert sources.notion_retry_at('invalid',clock=lambda:1000)==1900


def test_bounded_original_read_preserves_named_child_links():
    child=sources.NOTION_ROOT.rsplit('/',1)[0]+'/'+'a'*32
    def page(url):
        return '자료실', '기능명세서와 API 명세서가 있는 공개 자료실 본문이에요. '*5, [
            {'url':child,'title':'API 명세서'}, {'url':'https://evil.test/'+'a'*32,'title':'외부'},
            sources.NOTION_ROOT]
    def collect(url,**kwargs):
        assert kwargs=={'max_pages':1,'max_seconds':60}
        return sources.collect_notion(url,reader=page,**kwargs)
    result=reader.read_page(collector=collect)
    assert result['ok'] and len(result['documents'])==1
    assert result['documents'][0]['links']==[{'url':child,'title':'API 명세서'}]
    assert not result['coverage']['complete']


def test_queue_roundtrip_and_ephemeral_cleanup(tmp_path):
    def pause(_):
        assert reader.serve_once(tmp_path/'notion-read',reader=lambda url:{'ok':True,'requested_url':url,'documents':[]})
    result=reader.request_page(tmp_path,pause=pause)
    assert result['ok'] and result['requested_url']==sources.NOTION_ROOT
    assert {p.name for p in (tmp_path/'notion-read').iterdir()}=={reader.STATE_NAME,reader.LOCK_NAME}


def test_queue_failure_does_not_echo_sensitive_exception(tmp_path):
    def broken(url):raise ValueError('sensitive-body')
    response=reader.request_page(tmp_path,pause=lambda _:reader.serve_once(tmp_path/'notion-read',reader=broken))
    assert response=={'ok':False,'error':'reader_failed','documents':[]}
    assert 'sensitive' not in json.dumps(response)


def test_timeout_does_not_claim_page_absence(tmp_path):
    ticks=[0]
    def pause(_):ticks[0]+=1
    result=reader.request_page(tmp_path,timeout=2,clock=lambda:ticks[0],pause=pause)
    assert not result['ok'] and result['error']=='reader_timeout'
    assert not list((tmp_path/'notion-read').glob('*.request.json'))


def test_expired_or_malformed_requests_never_reach_browser(tmp_path):
    for i,payload in enumerate(({'url':sources.NOTION_ROOT,'expires':time.time()-1},
                                 {'url':sources.NOTION_ROOT,'expires':time.time()+999},
                                 {'url':sources.NOTION_ROOT,'expires':time.time()+60,'shell':'oops'})):
        reader.write_new(tmp_path/(f'{i:032x}.request.json'),payload)
    assert not reader.serve_once(tmp_path,reader=lambda _:pytest.fail('must not read expired request'))
    assert {p.name for p in tmp_path.iterdir()}=={reader.LOCK_NAME}


def test_symlink_queue_and_oversized_response_rejected(tmp_path):
    target=tmp_path/'target';target.mkdir()
    link=tmp_path/'notion-read';link.symlink_to(target)
    with pytest.raises(ValueError):reader.request_page(tmp_path)
    big=tmp_path/'big';big.write_bytes(b'x'*(reader.MAX_RESPONSE+1))
    with pytest.raises(ValueError):reader.read_json(big)


def test_bridge_uses_reader_without_exposing_shell(monkeypatch):
    calls=[]
    monkeypatch.setattr(reader,'request_page',lambda state,url:calls.append((state,url)) or {'ok':True,'documents':[]})
    result=invoke({'op':'read_notion'},runner=lambda *a,**kw:pytest.fail('no shell/subprocess for original reads'))
    assert result['untrusted_source_content']
    assert calls==[('/opt/data/pm-workspace',sources.NOTION_ROOT)]
    actor='12345'
    assert authorize('read_notion',platform='discord',guild_id=GUILD_ID,actor_id=actor,
                     get=lambda _: {'user':{'id':actor},'roles':[]})==frozenset()


def test_pacing_survives_worker_restart_and_duplicate_cache_is_exact(tmp_path, monkeypatch):
    monkeypatch.setenv('GUJEUK_READER_SHARED_QUEUE', '1')
    directory=tmp_path/'notion-read';directory.mkdir()
    now=[time.time()]
    calls=[]
    def browser(url):
        calls.append(url)
        return {'ok':True,'requested_url':url,'documents':[{'id':url,'url':url}],
                'coverage':{'kind':'notion','issues':[]}}
    def enqueue(url, number):
        request=directory/(f'{number:032x}.request.json')
        reader.write_new(request,{'url':url,'expires':time.time()+90})
        return request,request.with_name(request.name.replace('.request.','.response.'))
    view_a=sources.NOTION_ROOT+'?v='+'a'*32
    view_b=sources.NOTION_ROOT+'?v='+'b'*32
    _,response=enqueue(view_a,1)
    assert reader.serve_once(directory,reader=browser,clock=lambda:now[0])
    assert reader.read_json(response)['requested_url']==view_a
    assert response.stat().st_mode & 0o777==0o660
    assert (directory/reader.STATE_NAME).stat().st_mode & 0o777==0o600
    assert (directory/reader.LOCK_NAME).stat().st_mode & 0o777==0o600
    request,response=enqueue(view_a,2)
    assert reader.serve_once(directory,reader=browser,clock=lambda:now[0])
    assert reader.read_json(response)['cache_hit'] is True
    assert calls==[view_a]
    request,response=enqueue(view_b,3)
    assert not reader.serve_once(directory,reader=browser,clock=lambda:now[0])
    assert request.exists() and not response.exists()
    now[0]+=reader.PACE_SECONDS
    assert reader.serve_once(directory,reader=browser,clock=lambda:now[0])
    assert calls==[view_a,view_b]
    now[0]+=reader.CACHE_SECONDS
    reader.prune_reader_cache(directory,clock=lambda:now[0])
    assert reader.reader_state(directory)['cache'] is None


def test_429_cooldown_precedes_cache_and_preserves_retry_after(tmp_path):
    directory=tmp_path/'notion-read';directory.mkdir()
    now=[time.time()]
    calls=[]
    target=sources.NOTION_ROOT
    other=target+'?v='+'a'*32
    retry_at=now[0]+17
    def browser(url):
        calls.append(url)
        if url==other:
            return {'ok':False,'documents':[],'coverage':{'kind':'notion','issues':[
                {'status':429,'error':'SourceHTTPError','retry_at':retry_at}]}}
        return {'ok':True,'requested_url':url,'documents':[{'id':'root','url':url}],
                'coverage':{'kind':'notion','issues':[]}}
    def run(url,number):
        request=directory/(f'{number:032x}.request.json')
        reader.write_new(request,{'url':url,'expires':time.time()+90})
        assert reader.serve_once(directory,reader=browser,clock=lambda:now[0])
        return reader.read_json(request.with_name(request.name.replace('.request.','.response.')))
    run(target,1)
    now[0]+=reader.PACE_SECONDS
    assert run(other,2)['coverage']['issues'][0]['retry_at']==retry_at
    blocked=run(target,3)
    assert blocked['error']=='reader_cooldown'
    assert blocked['coverage']['issues'][0]['retry_at']==retry_at
    assert calls==[target,other]
    assert reader.reader_state(directory)['cooldown_until']==retry_at


def test_frontier_keeps_exact_retry_after_and_never_shortens_it(tmp_path):
    store=Workspace(tmp_path/'workspace.db')
    try:
        short_retry=time.time()+17
        def blocked(value):
            return {'requested_url':sources.NOTION_ROOT,'documents':[],
                    'coverage':{'kind':'notion','issues':[
                        {'status':429,'error':'SourceHTTPError','retry_at':value}]}}
        store.record_notion_read(blocked(short_retry))
        first=float(store.db.execute("SELECT value FROM workspace_meta WHERE key='notion_host_retry'").fetchone()[0])
        assert first==short_retry
        retry_at=time.time()+1200
        store.record_notion_read(blocked(retry_at))
        store.record_notion_read(blocked(time.time()+17))
        second=float(store.db.execute("SELECT value FROM workspace_meta WHERE key='notion_host_retry'").fetchone()[0])
        assert second==retry_at
    finally:
        store.close()
