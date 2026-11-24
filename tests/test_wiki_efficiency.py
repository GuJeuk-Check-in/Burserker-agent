from collections import Counter
from oijwef098234.gujeuk_ai.sources import document, NOTION_ROOT
from oijwef098234.gujeuk_ai.wiki import WikiWorkspace, update, private_write, render, validate_entries


def test_source_kinds_do_not_starve_each_other(tmp_path):
    store=WikiWorkspace(tmp_path/'workspace.db')
    for kind,url in [('discord','https://discord.com/channels/1413450780068937790/10/1'),('notion',NOTION_ROOT)]:
        store.ingest({'documents':[document(kind,str(i),url,'자료','업무 자료예요.') for i in range(10)],'coverage':{'kind':kind}})
    assert Counter(row['kind'] for row in store.pending_wiki(4))=={'discord':2,'notion':2}
    store.close()


def test_empty_sources_keep_raw_receipt_without_inference(tmp_path):
    class Never:
        def ask(self,*args):raise AssertionError('empty evidence must not invoke model')
        def close(self):pass
    store=WikiWorkspace(tmp_path/'workspace.db')
    store.ingest({'documents':[document('notion','1',NOTION_ROOT,'빈 페이지','  ')],'coverage':{'kind':'notion'}})
    result=update(store,tmp_path/'wiki',Never())
    assert result['updated']==1 and result['failed']==0
    assert result['model_calls']==0 and result['empty_sources']==1
    assert store.wiki_status()['pending_sources']==0
    assert len(list((tmp_path/'wiki/raw').glob('*.json')))==1
    assert store.search_wiki()==[]
    store.close()


def test_unchanged_export_preserves_inode_and_mtime(tmp_path):
    private_write(tmp_path,'index.md','same content')
    path=tmp_path/'index.md';before=path.stat()
    private_write(tmp_path,'index.md','same content')
    after=path.stat()
    assert (after.st_ino,after.st_mtime_ns)==(before.st_ino,before.st_mtime_ns)
    assert after.st_mode&0o777==0o600
    private_write(tmp_path,'index.md','changed')
    assert path.read_text()=='changed'


def test_new_observation_refreshes_coverage_without_rerender(tmp_path, monkeypatch):
    class Empty:
        def ask(self, *_): return '{"entries":[]}'
        def close(self): pass
    store = WikiWorkspace(tmp_path/'workspace.db')
    row = document('notion','1',NOTION_ROOT,'자료','공개 업무 자료')
    store.ingest({'documents':[row], 'coverage':{'kind':'notion','checked':'2025-01-01T00:00:00Z'}})
    assert update(store,tmp_path/'wiki',Empty())['updated'] == 1
    index = tmp_path/'wiki'/'index.md'
    before = index.stat().st_mtime_ns
    store.ingest({'documents':[row|{'observed_at':'2025-01-02T00:00:00Z'}],
        'coverage':{'kind':'notion','checked':'2025-01-02T00:00:00Z'}})
    import oijwef098234.gujeuk_ai.wiki as wiki
    monkeypatch.setattr(wiki, 'render', lambda *_: (_ for _ in ()).throw(AssertionError('idle rerender')))
    assert update(store,tmp_path/'wiki',Empty())['render_skipped']
    assert index.stat().st_mtime_ns == before
    assert '2025-01-02T00:00:00Z' in (tmp_path/'wiki'/'coverage.json').read_text()
    store.close()


def test_idle_repairs_missing_claim_page_after_crash(tmp_path):
    class Never:
        def ask(self, *_): raise AssertionError('acknowledged source should not rerun')
        def close(self): pass
    store = WikiWorkspace(tmp_path/'workspace.db')
    row = document('notion','1',NOTION_ROOT,'자료','공개 업무 자료')
    store.ingest({'documents':[row], 'coverage':{'kind':'notion'}})
    entries = validate_entries({'entries':[{'category':'task','title':'공개 업무',
        'claim':'공개 업무 자료예요.', 'quote':'공개 업무 자료', 'certainty':'fact',
        'person_id':''}]}, row)
    store.wiki_record(row, entries)
    root = tmp_path/'wiki'
    render(store, root)
    page = root/(entries[0]['entity']+'.md')
    page.unlink()
    result = update(store, root, Never())
    assert not result['render_skipped'] and result['updated'] == 0
    assert page.is_file()
    store.close()
