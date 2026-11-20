"""Native Discord poll ingestion, additive migration and AI evidence projection."""
import importlib.util
from pathlib import Path
import pytest
from oijwef098234.gujeuk_ai.runtime import discord_snapshot
from oijwef098234.gujeuk_ai.workspace import Workspace

PATH=Path(__file__).parents[1]/'services/gujeuk_pm/collector.py'
spec=importlib.util.spec_from_file_location('poll_collector',PATH)
assert spec is not None and spec.loader is not None
collector=importlib.util.module_from_spec(spec); spec.loader.exec_module(collector)
GUILD='1413450780068937790'


def poll_message():
    return {'id':'101','channel_id':'10','content':'','author':{'id':'9'},
        'poll':{'question':{'text':'방문 날짜'},'answers':[
            {'answer_id':1,'poll_media':{'text':'금요일'}},
            {'answer_id':2,'poll_media':{'text':'토요일'}}],
            'allow_multiselect':True,'expiry':'2026-12-01T00:00:00Z',
            'results':{'is_finalized':False,'answer_counts':[{'id':1,'count':3},{'id':2,'count':0}]}}}


def setup_store(tmp_path):
    store=collector.Store(tmp_path/'discord',GUILD)
    store.discover({'id':'99','type':4,'name':'법동 채팅 채널'})
    store.discover({'id':'10','type':0,'name':'일정','parent_id':'99'})
    return store


def test_poll_persists_and_projects_through_workspace_search(tmp_path):
    store=setup_store(tmp_path)
    store.save_page('10',[poll_message()],advance=False)
    row=store.recent()[0]
    assert row['poll']['answers'][0]['count']==3
    assert row['poll']['answers'][1]['count']==0
    assert row['poll']['expiry']=='2026-12-01T00:00:00Z'
    assert row['poll']['is_finalized'] is False
    assert row['channel_name']=='일정' and row['parent_name']=='법동 채팅 채널'
    snapshot=discord_snapshot(PATH,store.directory)
    doc=snapshot['documents'][0]
    assert doc['poll']['question']=='방문 날짜'
    assert '복수선택' in doc['content'] and '금요일' in doc['content']
    assert doc['poll_observed_at']==row['observed']
    workspace=Workspace(tmp_path/'workspace.db')
    workspace.ingest(snapshot)
    result=workspace.command({'op':'search','data':{'kind':'discord','query':'법동'}})
    assert isinstance(result, list)
    assert result[0]['poll']['answers'][0]['count']==3
    workspace.close(); store.db.close()


def test_unknown_results_are_not_zero_and_refresh_is_not_duplicate(tmp_path):
    store=setup_store(tmp_path)
    message=poll_message(); message['poll']['results']=None
    store.save_page('10',[message])
    assert store.recent()[0]['poll']['answers'][0]['count'] is None
    assert store.recent()[0]['poll']['is_finalized'] is None
    message['poll']['results']={'is_finalized':True,'answer_counts':[{'id':1,'count':7}]}
    store.save_page('10',[message],advance=False)
    rows=store.recent()
    assert len(rows)==1 and rows[0]['poll']['answers'][0]['count']==7
    assert rows[0]['poll']['is_finalized'] is True
    workspace=Workspace(tmp_path/'workspace.db')
    first=workspace.model_digest(discord_snapshot(PATH,store.directory)['documents'])
    store.save_page('10',[message],advance=False)
    assert first==workspace.model_digest(discord_snapshot(PATH,store.directory)['documents'])
    workspace.close(); store.db.close()


def test_additive_poll_migration_keeps_old_messages_and_cursor(tmp_path):
    store=setup_store(tmp_path)
    store.save_page('10',[{'id':'102','content':'보존할 본문'}])
    cursor=store.cursor('10')
    store.db.execute('DROP TABLE message_polls'); store.db.commit(); store.db.close()
    readonly=collector.Store(tmp_path/'discord',GUILD,read_only=True)
    assert readonly.changed()[0]['poll'] is None
    readonly.db.close()
    migrated=collector.Store(tmp_path/'discord',GUILD)
    assert migrated.cursor('10')==cursor
    assert migrated.recent()[0]['content']=='보존할 본문'
    migrated.save_page('10',[poll_message()],advance=False)
    assert migrated.status()['messages']==2
    migrated.db.close()


def test_poll_page_rolls_back_with_invalid_channel(tmp_path):
    store=setup_store(tmp_path)
    with pytest.raises(ValueError):
        store.save_page('10',[poll_message(),{'id':'102','channel_id':'wrong'}])
    assert store.recent()==[]
    assert store.db.execute('SELECT count(*) FROM message_polls').fetchone()[0]==0
    store.db.close()
