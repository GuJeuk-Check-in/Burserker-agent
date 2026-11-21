from pathlib import Path
import pytest
from oijwef098234.gujeuk_ai.sources import document, NOTION_ROOT, collect_notion
from oijwef098234.gujeuk_ai.wiki import WikiWorkspace, validate_entries, render
from oijwef098234.gujeuk_ai.workspace import Workspace
from oijwef098234.gujeuk_ai.notion_frontier import crawl


def test_person_preferences_and_observed_style_have_provenance():
    row=document('discord','1','https://discord.com/channels/1413450780068937790/2/1',
        '팀 대화','저는 커피를 좋아하고 요청은 짧게 남겨 주세요.',author_id='3')
    base={'category':'personal_preference','title':'작성자 취향','claim':'커피를 좋아한다고 밝혔어요.',
        'quote':'커피를 좋아하고','certainty':'fact','person_id':'3'}
    assert validate_entries({'entries':[base]},row)
    assert validate_entries({'entries':[base|{'category':'communication_style','certainty':'inference',
        'claim':'이 메시지는 짧은 요청형 문장을 사용해요.','quote':'짧게 남겨 주세요.'}]},row)
    with pytest.raises(ValueError):
        validate_entries({'entries':[base|{'claim':'정치 성향을 추정해요.'}]},row)
    with pytest.raises(ValueError):
        validate_entries({'entries':[base|{'person_id':'999'}]},row)


def test_changed_sources_do_not_serve_stale_claims(tmp_path):
    store=WikiWorkspace(tmp_path/'state.db')
    row=document('notion','1',NOTION_ROOT,'업무','백엔드 명세서를 확인해 주세요.')
    store.ingest({'documents':[row],'coverage':{'kind':'notion'}})
    entries=validate_entries({'entries':[{'category':'task','title':'명세서 확인','claim':'명세서 확인 요청이 있어요.',
        'quote':'명세서를 확인해 주세요.','certainty':'fact','person_id':''}]},row)
    store.wiki_record(row,entries)
    root=tmp_path/'wiki';render(store,root)
    old=root/(entries[0]['entity']+'.md')
    assert store.search_wiki('명세서')
    store.ingest({'documents':[row|{'content':'기존 요청은 새 문서로 교체됐어요.'}],'coverage':{'kind':'notion'}})
    assert not store.search_wiki('명세서')
    render(store,root)
    assert '이전 주장을 최신 사실로 사용하지 마세요' in old.read_text()
    store.close()


def test_discovery_beyond_display_links_is_persisted(tmp_path):
    store=Workspace(tmp_path/'state.db')
    urls=['https://magical-sovereign-7b8.notion.site/'+format(i,'032x') for i in range(1,151)]
    row=document('notion','root',NOTION_ROOT,'시작 페이지','링크 목록은 화면 출력과 별개로 모두 탐색 대기열에 넣어요.')
    store.record_notion_read({'requested_url':NOTION_ROOT,'documents':[row],
        'discovered_urls':urls,'coverage':{'kind':'notion','issues':[]}})
    assert store.notion_frontier_status()['known_pages']==151
    store.close()


def test_rate_limit_blocks_host_and_remains_visible(tmp_path):
    store=Workspace(tmp_path/'state.db')
    other='https://magical-sovereign-7b8.notion.site/'+'a'*32
    store.schedule_notion([other])
    store.record_notion_read({'requested_url':NOTION_ROOT,'documents':[],
        'coverage':{'kind':'notion','issues':[{'error':'SourceHTTPError','status':429,'retry_at':0}]}})
    assert store.due_notion() is None
    result=crawl(store,tmp_path,reader=lambda *_:pytest.fail('must respect host cooldown'))
    assert result['coverage']['issues'][0]['reason']=='known_pages_awaiting_retry'
    assert store.notion_frontier_status()['failures']=={'SourceHTTPError:429':1}
    store.close()
