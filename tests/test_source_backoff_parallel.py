import importlib.util
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
import pytest
from oijwef098234.gujeuk_ai import runtime, figma
from oijwef098234.gujeuk_ai.wiki import WikiWorkspace, update
from oijwef098234.gujeuk_ai.sources import document, NOTION_ROOT, SourceHTTPError


def test_rate_limit_survives_restart_without_blocking_other_source(tmp_path,monkeypatch):
    path=tmp_path/'workspace.db'
    store=WikiWorkspace(path)
    old={'kind':'figma','checked':'original-check','issues':[{'status':429,'retry_at':1000}]}
    store.ingest({'documents':[],'coverage':old});store.close()
    store=WikiWorkspace(path);calls=[]
    monkeypatch.setattr(runtime.time,'time',lambda:500)
    def fetch():calls.append(1);return {'documents':[],'coverage':{'kind':'figma','issues':[]}}
    def discord():return {'documents':[],'coverage':{'kind':'discord','issues':[]}}
    result=runtime.tick(store,notion=None,github=None,figma=fetch,discord=discord)
    assert calls==[] and result['figma']['deferred_until']==1000
    assert result['discord']['documents']==0
    assert store.coverage()['figma']['checked']=='original-check'
    monkeypatch.setattr(runtime.time,'time',lambda:1001)
    runtime.tick(store,notion=None,github=None,figma=fetch)
    assert calls==[1] and store.coverage()['figma']['issues']==[]
    store.close()


@pytest.mark.parametrize('delay',['172800','nan','invalid','inf'])
def test_figma_retry_header_is_safe_and_not_shortened_to_one_day(monkeypatch,delay):
    class Opener:
        def open(self,*a,**kw):raise HTTPError('https://api.figma.com',429,'hidden',{'Retry-After':delay},None)
    monkeypatch.setattr(figma,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(figma.time,'time',lambda:1000)
    with pytest.raises(SourceHTTPError) as error:figma.figma_get('/v1/files/'+figma.FILE_KEY,'not-a-real-credential')
    assert error.value.retry_at==(173800 if delay=='172800' else 1300)


def test_parallel_wiki_uses_distinct_agents_and_main_thread_storage(tmp_path,monkeypatch):
    store=WikiWorkspace(tmp_path/'workspace.db')
    store.ingest({'documents':[document('notion',str(i),NOTION_ROOT,'원문','예약 기능 구현') for i in range(4)],'coverage':{'kind':'notion'}})
    barrier=threading.Barrier(2);agents=[];main=threading.get_ident()
    record=store.wiki_record
    def checked_record(*args):assert threading.get_ident()==main;return record(*args)
    monkeypatch.setattr(store,'wiki_record',checked_record)
    class Engine:
        def __init__(self):self.closed=False;agents.append(self)
        def ask(self,prompt,*args):
            assert threading.get_ident()!=main
            row=json.loads(prompt.split('<untrusted-source>\n')[1].split('\n</untrusted-source>')[0])
            barrier.wait(timeout=3)
            if row['id']=='0':raise ValueError('isolated failure')
            return '{"entries":[]}'
        def close(self):self.closed=True
    result=update(store,tmp_path/'wiki',None,batch=4,workers=2,engine_factory=Engine)
    assert result['updated']==3 and result['failed']==1
    assert len(agents)==4 and all(a.closed for a in agents)
    assert store.wiki_status()['pending_sources']==1
    store.close()


def test_figma_metadata_probe_is_not_claimed_as_node_read(tmp_path):
    spec=importlib.util.spec_from_file_location('verify_figma',Path(__file__).parents[1]/'scripts/verify_figma.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    path=tmp_path/'token';path.write_text('not-a-real-credential')
    result=module.verify(path,get=lambda route,key:{'file':{'name':'fixture'}})
    assert result['metadata_access'] is True and result['selected_node_verified'] is False
