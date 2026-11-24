import hashlib
import json

import pytest

from oijwef098234.gujeuk_ai.export_reconcile import reconcile_export
from oijwef098234.gujeuk_ai.wiki import WikiWorkspace, render, update, validate_entries


PAGE = 'a' * 32
ROW = 'b' * 32
HIDDEN = 'c' * 32
URL = 'https://magical-sovereign-7b8.notion.site/' + PAGE
WHEN = '2025-01-02T00:00:00+00:00'


def reconcile(store, root, approval, stage):
    return reconcile_export(store, root, approval, stage, trusted_page_ids={PAGE})


def proof(value):
    return {'sha256': hashlib.sha256(value.encode()).hexdigest(), 'observed_at': WHEN}


def fixture(tmp_path):
    root = tmp_path / 'synthetic-export'
    root.mkdir()
    page = {'id': PAGE, 'modified_at': WHEN,
        'fields': {'Title': '공개 계획', 'Hidden': '비공개 내부 정보'},
        'rows': [{'id': ROW, 'fields': {'Name': '공개 작업', 'Hidden': '숨김 데이터'}},
                 {'id': HIDDEN, 'fields': {'Name': '비공개 행'}}]}
    (root / 'page.json').write_text(json.dumps(page, ensure_ascii=False))
    approved = {'version': 1, 'pages': [{'id': PAGE, 'url': URL, 'file': 'page.json',
        'fields': {'Title': proof('공개 계획')},
        'rows': [{'id': ROW, 'fields': {'Name': proof('공개 작업')}}]}]}
    approval = tmp_path / 'approval.json'
    approval.write_text(json.dumps(approved, ensure_ascii=False))
    return root, approval, page, approved


class EmptyEngine:
    def ask(self, prompt, *_):
        assert '비공개' not in prompt and 'Hidden' not in prompt and HIDDEN not in prompt
        return '{"entries":[]}'
    def close(self):
        pass


def test_repeatable_private_reconciliation_and_incremental_wiki(tmp_path, monkeypatch):
    root, approval, page, approved = fixture(tmp_path)
    store = WikiWorkspace(tmp_path / 'state.db')
    stage = tmp_path / 'private-stage'
    result = reconcile(store, root, approval, stage)
    assert result['changed_documents'] == 2
    staged = (stage / 'approved.json').read_text()
    assert '비공개' not in staged and HIDDEN not in staged and 'Hidden' not in staged
    assert stage.stat().st_mode & 0o777 == 0o700
    assert (stage / 'approved.json').stat().st_mode & 0o777 == 0o600
    assert store.wiki_status()['pending_sources'] == 2
    first = update(store, tmp_path / 'wiki', EmptyEngine())
    assert first['updated'] == 2 and not first['render_skipped']
    assert reconcile(store, root, approval, stage)['changed_documents'] == 0
    import oijwef098234.gujeuk_ai.wiki as wiki
    monkeypatch.setattr(wiki, 'render', lambda *_: pytest.fail('idle render'))
    assert update(store, tmp_path / 'wiki', EmptyEngine())['render_skipped']
    monkeypatch.undo()
    page['rows'][0]['fields']['Name'] = '공개 작업 변경'
    approved['pages'][0]['rows'][0]['fields']['Name'] = proof('공개 작업 변경')
    (root / 'page.json').write_text(json.dumps(page, ensure_ascii=False))
    approval.write_text(json.dumps(approved, ensure_ascii=False))
    assert reconcile(store, root, approval, stage)['changed_documents'] == 1
    assert update(store, tmp_path / 'wiki', EmptyEngine())['updated'] == 1
    # A crash after the DB acknowledgement leaves a missing receipt or file.
    (tmp_path / 'wiki' / 'render-state.json').unlink()
    assert not update(store, tmp_path / 'wiki', EmptyEngine())['render_skipped']
    store.close()


def test_public_id_does_not_authorize_hidden_fields_or_rows(tmp_path):
    root, approval, page, approved = fixture(tmp_path)
    store = WikiWorkspace(tmp_path / 'state.db')
    reconcile(store, root, approval, tmp_path / 'stage')
    assert len(store.documents(10)) == 2
    # The hidden row is present in the export but absent from approved evidence.
    assert all(HIDDEN not in doc['id'] for doc in store.documents(10))
    row = next(doc for doc in store.documents(10) if ROW in doc['id'])
    entries = validate_entries({'entries': [{'category': 'task', 'title': '공개 작업',
        'claim': '공개 작업이 있어요.', 'quote': '공개 작업', 'certainty': 'fact',
        'person_id': ''}]}, row)
    store.wiki_record(row, entries)
    render(store, tmp_path / 'wiki')
    assert store.search_wiki('공개 작업')
    approved['pages'][0]['rows'] = []
    approval.write_text(json.dumps(approved))
    result = reconcile(store, root, approval, tmp_path / 'stage')
    assert result['withdrawn_documents'] == 1
    assert len(store.documents(10)) == 1
    assert not store.search_wiki('공개 작업')
    update(store, tmp_path / 'wiki', EmptyEngine())
    assert '이전 주장을 최신 사실로 사용하지 마세요' in (tmp_path / 'wiki' / (entries[0]['entity'] + '.md')).read_text()
    store.close()


def test_trusted_page_list_is_independent_of_public_approval(tmp_path):
    root, approval, _, _ = fixture(tmp_path)
    store = WikiWorkspace(tmp_path / 'state.db')
    with pytest.raises(ValueError, match='untrusted_page_id'):
        reconcile_export(store, root, approval, tmp_path / 'stage', trusted_page_ids={'d' * 32})
    assert store.documents(10) == []
    store.close()


@pytest.mark.parametrize('tamper', ['hash', 'secret', 'personal', 'key_field', 'stale', 'row', 'path'])
def test_bad_evidence_fails_closed_without_database_mutation(tmp_path, tamper):
    root, approval, page, approved = fixture(tmp_path)
    store = WikiWorkspace(tmp_path / 'state.db')
    stage = tmp_path / 'stage'
    reconcile(store, root, approval, stage)
    before = (stage / 'approved.json').read_bytes()
    if tamper == 'hash':
        page['fields']['Title'] = '다른 값'
    elif tamper == 'secret':
        page['fields']['Title'] = 'api_key = synthetic-secret'
        approved['pages'][0]['fields']['Title'] = proof(page['fields']['Title'])
    elif tamper == 'personal':
        page['fields']['Title'] = 'person@example.invalid'
        approved['pages'][0]['fields']['Title'] = proof(page['fields']['Title'])
    elif tamper == 'key_field':
        page['fields']['API Token'] = 'synthetic-value'
        approved['pages'][0]['fields']['API Token'] = proof('synthetic-value')
    elif tamper == 'stale':
        page['modified_at'] = '2025-01-03T00:00:00Z'
    elif tamper == 'row':
        approved['pages'][0]['rows'][0]['id'] = HIDDEN
    else:
        approved['pages'][0]['file'] = '../outside.json'
    (root / 'page.json').write_text(json.dumps(page))
    approval.write_text(json.dumps(approved))
    if tamper == 'row':
        # A row ID still needs its own matching public field evidence.
        pass
    with pytest.raises(ValueError):
        reconcile(store, root, approval, stage)
    assert (stage / 'approved.json').read_bytes() == before
    assert len(store.documents(10)) == 2
    store.close()
