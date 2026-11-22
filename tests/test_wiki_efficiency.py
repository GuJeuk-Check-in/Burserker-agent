from collections import Counter
from oijwef098234.gujeuk_ai.sources import document, NOTION_ROOT
from oijwef098234.gujeuk_ai.wiki import WikiWorkspace, update, private_write


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
