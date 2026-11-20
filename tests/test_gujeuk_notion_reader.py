import json
from pathlib import Path
import time

import pytest

from oijwef098234.gujeuk_ai import notion_reader as reader
from oijwef098234.gujeuk_ai import sources
from oijwef098234.gujeuk_ai.access import authorize, GUILD_ID
from oijwef098234.gujeuk_ai.bridge import invoke


def test_reader_keeps_selected_database_page_and_rejects_offsite():
    url=sources.NOTION_ROOT+'?v='+'a'*32+'&p='+'b'*32+'&pm=s'
    assert sources.notion_url(url)==sources.NOTION_ROOT+'?p='+'b'*32+'&pm=s'
    assert sources.page_id(sources.notion_url(url))=='b'*32
    for bad in ('http://127.0.0.1/'+'a'*32,
                'https://magical-sovereign-7b8.notion.site.evil.test/'+'a'*32,
                'https://magical-sovereign-7b8.notion.site@evil.test/'+'a'*32,
                'file:///tmp/'+'a'*32, sources.NOTION_ROOT+'?p=not-a-page'):
        with pytest.raises(ValueError):reader.read_page(bad)


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
    assert not list((tmp_path/'notion-read').iterdir())


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
    assert not list((tmp_path/'notion-read').iterdir())


def test_expired_or_malformed_requests_never_reach_browser(tmp_path):
    for i,payload in enumerate(({'url':sources.NOTION_ROOT,'expires':time.time()-1},
                                 {'url':sources.NOTION_ROOT,'expires':time.time()+999},
                                 {'url':sources.NOTION_ROOT,'expires':time.time()+60,'shell':'oops'})):
        reader.write_new(tmp_path/(f'{i:032x}.request.json'),payload)
    assert not reader.serve_once(tmp_path,reader=lambda _:pytest.fail('must not read expired request'))
    assert not list(tmp_path.iterdir())


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
