import json
from pathlib import Path
import pytest
from oijwef098234.gujeuk_ai.sources import document, NOTION_ROOT
from oijwef098234.gujeuk_ai.wiki import WikiWorkspace, update, validate_entries
from oijwef098234.gujeuk_ai.access import filter_evidence, GUILD_ID
from oijwef098234.gujeuk_ai.figma import collect_figma, FILE_KEY


def entry(**kw):
    return {'category':'task','title':'예약 기능','claim':'예약 기능을 구현하기로 했어요.',
        'quote':'예약 기능 구현','certainty':'fact','person_id':'', **kw}


class Engine:
    def ask(self, prompt, callback):
        assert '<untrusted-source>' in prompt
        assert callback('permission', {}) is None
        return json.dumps({'entries':[entry()]}, ensure_ascii=False)
    def close(self): pass


def test_wiki_durable_full_queue_edit_and_restart(tmp_path):
    path = tmp_path/'workspace.db'
    store = WikiWorkspace(path)
    docs = [document('notion', str(i), NOTION_ROOT, '예약', '예약 기능 구현을 논의했어요.') for i in range(12)]
    store.ingest({'documents':docs,'coverage':{'kind':'notion'}})
    assert update(store, tmp_path/'wiki', Engine(), batch=8)['updated']==8
    assert store.wiki_status()['pending_sources']==4
    store.close()
    store=WikiWorkspace(path)
    assert update(store,tmp_path/'wiki',Engine(),batch=8)['updated']==4
    assert update(store,tmp_path/'wiki',Engine(),batch=8)['updated']==0
    assert (tmp_path/'wiki/index.md').stat().st_mode & 0o777==0o600
    assert len(list((tmp_path/'wiki/raw').glob('*.json')))==12
    changed=docs[0]|{'content':'예약 기능을 취소했어요.'}
    store.ingest({'documents':[changed],'coverage':{'kind':'notion'}})
    class Retract:
        def ask(self,*_):return '{"entries":[]}'
        def close(self):pass
    assert update(store,tmp_path/'wiki',Retract())['updated']==1
    assert len(store.search_wiki('예약'))==10  # bounded query, 11 valid sources remain
    assert store.wiki_status()['pending_sources']==0
    assert len(list((tmp_path/'wiki/raw').glob('*.json')))==13
    store.close()


def test_quotes_and_person_inferences_rejected():
    source=document('discord','1',f'https://discord.com/channels/{GUILD_ID}/10/1','원문',
        '짧은 답변을 좋아해요. 예약 기능 구현',author_id='42')
    with pytest.raises(ValueError):validate_entries({'entries':[entry(quote='없는 문장')]},source)
    with pytest.raises(ValueError):validate_entries({'entries':[entry(category='work_preference',person_id='43')]},source)
    with pytest.raises(ValueError):validate_entries({'entries':[entry(category='work_preference',person_id='42',certainty='inference')]},source)
    assert validate_entries({'entries':[entry(category='communication_preference', person_id='42', quote='짧은 답변을 좋아해요.')]},source)


def test_wiki_private_channel_claim_uses_same_access_filter():
    doc=document('discord','1',f'https://discord.com/channels/{GUILD_ID}/10/1','비공개 근거','위키 주장',wiki=True)
    def denied(_):raise PermissionError()
    result=filter_evidence({'result':{'documents':[doc]}},frozenset(),'42',get=denied)
    assert result['result']['documents']==[]


def test_failed_source_does_not_block_others_and_no_ack(tmp_path):
    store=WikiWorkspace(tmp_path/'workspace.db')
    store.ingest({'documents':[document('notion','1',NOTION_ROOT,'a','예약 기능 구현')], 'coverage':{'kind':'notion'}})
    class Bad:
        def ask(self,*_):return 'not JSON'
        def close(self):pass
    result=update(store,tmp_path/'wiki',Bad())
    assert result['failed']==1 and result['pending_sources']==1
    assert store.pending_wiki()==[]  # persisted backoff, no hot retry loop
    store.close()


def test_missing_figma_credentials_is_not_connected(tmp_path):
    result=collect_figma(token_file=tmp_path/'missing')
    assert result['coverage']['issues']==['credential_missing']
    assert not result['documents']


def test_figma_full_tree_and_text_chunks(tmp_path):
    token=tmp_path/'token';token.write_text('unit-test-not-a-credential')
    def get(path,key):
        assert path=='/v1/files/'+FILE_KEY
        return {'version':'unit-version','document':{'id':'0:0','name':'파일','type':'DOCUMENT',
            'children':[{'id':'9353:163','name':'설계','characters':'예약 기능 '*9000,'type':'TEXT'}]}}
    snapshot=collect_figma(token_file=token,get=get)
    assert len(snapshot['documents'])>2
    assert all(len(d['content'])<=18000 for d in snapshot['documents'])
    assert snapshot['coverage']['tree_cycle_finished']
