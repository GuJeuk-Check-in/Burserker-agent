import json
from datetime import datetime, timedelta, timezone

import pytest

from oijwef098234.gujeuk_ai.workspace import Workspace
from oijwef098234.gujeuk_ai.sources import NOTION_ROOT, document


@pytest.fixture
def store(tmp_path):
    result = Workspace(tmp_path / 'workspace.db')
    yield result
    result.close()


def propose(store, ident, patch, key, url=NOTION_ROOT):
    return store.command({'id': key, 'op': 'propose', 'data': {
        'item_id': ident, 'version': store.get(ident).version, 'patch': patch, 'url': url}})


def legacy(store, ident, patch, key, *, version=None, url=NOTION_ROOT, created=''):
    item = store.get(ident)
    with store.db:
        return store.db.execute('''INSERT INTO proposals(source_key,item_id,base_version,data,url,created_at)
            VALUES(?,?,?,?,?,?)''', (key, ident, item.version if version is None else version,
            json.dumps(item.editable() | patch), url, created)).lastrowid


def test_exact_dedup_across_request_ids_and_restart(store):
    ident = store.add({'title': '업무'})
    first = propose(store, ident, {'status': 'doing'}, 'one')
    second = propose(store, ident, {'status': 'doing'}, 'two')
    assert first['proposal_id'] == second['proposal_id']
    assert second['deduplicated'] and len(store.proposals()) == 1
    assert store.get(ident).status == 'blocked'
    reopened = Workspace(store.path)
    try:
        assert propose(reopened, ident, {'status': 'doing'}, 'three')['proposal_id'] == first['proposal_id']
        assert reopened.db.execute('SELECT count(*) FROM proposal_observations').fetchone()[0] == 3
    finally:
        reopened.close()


def test_automated_note_variants_keep_full_observations(store):
    ident = store.add({'title': '업무'})
    docs = [document('notion', 'one', NOTION_ROOT, '원문', '진행 변경 근거')]
    for digest, note in [('digest1', '첫 근거'), ('digest2', '새 근거')]:
        store.apply_analysis(digest, {'actions': [{'url': NOTION_ROOT, 'item_id': ident,
            'version': 1, 'data': {'status': 'doing', 'note': note}}]}, docs)
    assert len(store.proposals()) == 1
    observations = store.db.execute('SELECT data FROM proposal_observations ORDER BY source_key').fetchall()
    assert {json.loads(row[0])['note'] for row in observations} == {'첫 근거', '새 근거'}
    assert store.get(ident).note == '' and store.get(ident).status == 'blocked'
    # An explicit human note is not silently deduplicated by the automated heuristic.
    propose(store, ident, {'status': 'doing', 'note': '사용자 메모'}, 'human')
    assert len(store.proposals()) == 2


def test_noop_no_proposal_and_receipt_reuse_rejected(store):
    ident = store.add({'title': '업무'})
    assert propose(store, ident, {'status': 'blocked'}, 'noop')['skipped'] == 'unchanged'
    assert store.proposals() == []
    propose(store, ident, {'status': 'doing'}, 'key')
    with pytest.raises(ValueError):
        propose(store, ident, {'status': 'done'}, 'key')


def test_different_targets_intents_and_sources_stay_separate(store):
    one = store.add({'title': '같은 제목'})
    two = store.add({'title': '같은 제목'})
    propose(store, one, {'status': 'doing'}, 'a')
    propose(store, one, {'status': 'done'}, 'b')
    propose(store, two, {'status': 'doing'}, 'c')
    propose(store, one, {'status': 'doing'}, 'd', url=NOTION_ROOT + '?p=other')
    assert store.proposal_review()['counts']['review_groups'] == 4


def test_legacy_non_destructive_projection_and_unknown_age(store):
    ident = store.add({'title': '업무'})
    ids = [legacy(store, ident, {'status': 'doing'}, 'a'),
           legacy(store, ident, {'status': 'doing'}, 'b'),
           legacy(store, ident, {'status': 'doing', 'note': '다른 설명'}, 'c'),
           legacy(store, ident, {}, 'd'),
           legacy(store, ident, {'status': 'done'}, 'e', version=0)]
    before = [dict(row) for row in store.db.execute('SELECT * FROM proposals ORDER BY id')]
    review = store.proposal_review()
    counts = review['counts']
    assert {key: counts[key] for key in ('pending_records', 'review_groups', 'duplicate_records',
        'note_variants', 'no_change', 'stale_version', 'age_unknown')} == {
        'pending_records': 5, 'review_groups': 1, 'duplicate_records': 1,
        'note_variants': 1, 'no_change': 1, 'stale_version': 1, 'age_unknown': 5}
    assert review['groups'][0]['review_member_ids'] == ids[:3]
    assert review['groups'][0]['review_flags'] == ['note_variants_require_review']
    assert store.snapshot()['proposals'] == review['groups']
    assert store.source_status()['pending_proposals'] == 1
    assert store.source_status()['proposal_review']['counts']['pending_records'] == 5
    assert [dict(row) for row in store.db.execute('SELECT * FROM proposals ORDER BY id')] == before
    assert not review['automatic_resolution']


def test_known_old_age_does_not_auto_expire(store):
    ident = store.add({'title': '업무'})
    old = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    legacy(store, ident, {'status': 'doing'}, 'old', created=old)
    review = store.proposal_review()
    assert review['counts']['older_than_14_days'] == 1
    assert review['counts']['review_groups'] == 1
    assert len(store.proposals()) == 1


def test_new_item_legacy_proposal_not_orphaned(store):
    ident = store.add({'title': '업무'})
    pid = legacy(store, ident, {'title': '새 업무'}, 'new')
    with store.db:
        store.db.execute('UPDATE proposals SET item_id=0 WHERE id=?', (pid,))
    assert store.proposal_review()['counts']['review_groups'] == 1


def test_analysis_rolls_back_new_observations_on_bad_estimate(store):
    ident = store.add({'title': '업무'})
    docs = [document('notion', 'one', NOTION_ROOT, '원문', '진행 변경 근거')]
    with pytest.raises(ValueError):
        store.apply_analysis('bad', {'actions': [{'url': NOTION_ROOT, 'item_id': ident,
            'version': 1, 'data': {'status': 'doing'}}], 'estimates': [{'evidence': ['bad']}]}, docs)
    assert store.proposals() == []
    assert store.db.execute('SELECT count(*) FROM proposal_observations').fetchone()[0] == 0
    assert not store.analyzed('bad')


def test_note_only_automated_repeats_preserve_variants_without_queue_growth(store):
    ident = store.add({'title': '업무'})
    store.propose_change(ident, 1, {'note': '근거1'}, source_key='auto1', automated=True)
    store.propose_change(ident, 1, {'note': '근거2'}, source_key='auto2', automated=True)
    assert len(store.proposals()) == 1
    review = store.proposal_review()
    assert review['counts']['observation_variants'] == 1
    assert review['groups'][0]['review_flags'] == ['note_variants_require_review']


def test_age_from_recorded_analysis_run_not_calendar_due_date(store):
    ident = store.add({'title': '업무', 'due': '2020-01-01'})
    digest = 'a' * 64
    timestamp = datetime.now(timezone.utc).isoformat()
    with store.db:
        store.db.execute('INSERT INTO model_runs VALUES(?,?)', (digest, timestamp))
    legacy(store, ident, {'status': 'doing'}, f'request:ai:{digest}:0')
    review = store.proposal_review()
    assert review['counts']['age_unknown'] == 0
    assert review['counts']['older_than_14_days'] == 0


def test_concurrent_requests_cannot_duplicate_proposal(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    ident = store.add({'title': '업무'})
    barrier = Barrier(2)
    path = store.path
    def request(key):
        other = Workspace(path)
        try:
            barrier.wait(timeout=5)
            return propose(other, ident, {'status': 'doing'}, key)['proposal_id']
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(request, ['concurrent1', 'concurrent2']))
    assert ids[0] == ids[1]
    assert len(store.proposals()) == 1
