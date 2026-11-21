import importlib.util
from pathlib import Path
from oijwef098234.gujeuk_ai.sources import document, NOTION_ROOT
from oijwef098234.gujeuk_ai.workspace import Workspace
from oijwef098234.gujeuk_ai.notion_frontier import crawl


def test_notion_frontier_survives_restart_and_records_live_read(tmp_path):
    path=tmp_path/'workspace.db'
    store=Workspace(path)
    child=NOTION_ROOT.rsplit('/',1)[0]+'/API-'+'b'*32
    doc=document('notion','root',NOTION_ROOT,'자료실','공개된 자료실 본문을 읽었어요.',links=[{'url':child,'title':'API'}])
    result={'requested_url':NOTION_ROOT,'documents':[doc], 'coverage':{'kind':'notion'}}
    store.record_notion_read(result)
    assert store.notion_frontier_status()['known_pages']==2
    store.close()
    store=Workspace(path)
    assert store.due_notion()==child
    calls=[]
    def blocked(state,url):
        calls.append(url)
        return {'documents':[],'coverage':{'kind':'notion','issues':[{'status':429}]}}
    snapshot=crawl(store,tmp_path,reader=blocked)
    assert calls==[child]
    assert snapshot['coverage']['frontier']['states']['failed']==1
    assert not snapshot['coverage']['complete']
    store.close()


def test_full_discord_history_separate_cursor_and_restart(tmp_path):
    spec=importlib.util.spec_from_file_location('collector_history',Path(__file__).parents[1]/'services/gujeuk_pm/collector.py')
    assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    directory=tmp_path/'discord'
    store=mod.Store(directory,'1');store.enable_all_history()
    seed=store.initial_after()
    messages=[{'id':str(i),'channel_id':'10','content':'업무','author':{'id':'42'}}
        for i in range(seed-230,seed+3) if i!=seed]
    class API:
        def get(self,path,**params):
            if path=='/guilds/1/channels':return [{'id':'10','guild_id':'1','type':0,'name':'work'}]
            if path=='/guilds/1/threads/active' or '/archived/' in path:return {'threads':[],'has_more':False}
            if path.endswith('/messages'):
                if 'after' in params:return [m for m in messages if int(m['id'])>int(params['after'])][:100]
                if 'before' in params:return [m for m in reversed(messages) if int(m['id'])<int(params['before'])][:100]
                return list(reversed(messages[-100:]))
            raise AssertionError(path)
    mod.Collector(API(),store,max_pages=1).tick()
    assert store.cursor('10')==str(seed+2)
    store.db.close()
    store=mod.Store(directory,'1')
    for _ in range(3):mod.Collector(API(),store,max_pages=1).tick()
    assert store.cursor('10')==str(seed+2)
    assert store.status()['messages']==232
    assert store.status()['older_history_remaining']==0
    store.db.close()
