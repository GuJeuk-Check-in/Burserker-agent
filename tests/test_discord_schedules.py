"""Real SQLite scheduling/leases; fake REST/inference. Never post to Discord."""
import copy
from datetime import datetime
import json
import pytest
from test_discord_messaging import Discord
from oijwef098234.gujeuk_ai import schedules as s
from oijwef098234.gujeuk_ai.messaging import message_data, Targets, MessageStore

CLOCK = datetime.fromisoformat('2026-11-24T12:00:00+09:00').timestamp()
QUOTE = '방문 회고와 변경사항을 공유해요.'
URL = f'https://discord.com/channels/{s.GUILD_ID}/456/987'


class Rest(Discord):
    def get(self, path):
        if path == '/channels/456/messages?limit=50':
            return [{'id': '987', 'channel_id': '456', 'content': QUOTE, 'timestamp': '2026-11-17T03:00:00Z'}]
        return super().get(path)


class Engine:
    def __init__(self):
        self.prompts = []
        self.hook = lambda: None
        self.result = {'agenda': [{'title': '방문 회고와 변경사항 공유', 'url': URL, 'quote': QUOTE}]}
    def ask(self, prompt):
        self.prompts.append(prompt)
        self.hook()
        return json.dumps(self.result, ensure_ascii=False)
    def close(self):
        pass


class Workspace:
    def __init__(self):
        self.queries = []
    def search_documents(self, query, kind, limit):
        self.queries.append((query, kind, limit))
        return []


@pytest.fixture
def setup(tmp_path):
    fake, engine, workspace = Rest(), Engine(), Workspace()
    fake.content = '<@&555> 오늘 9시 30분 회의해요. 참여 여부를 알려주세요.\n\n회의 안건\n1. 예전 안건\n2. 기타의견'
    store = s.ScheduleStore(tmp_path/'outbox.db')
    payload = message_data({'channel_id': '456', 'content': fake.content, 'mention_roles': ['555'], 'reactions': ['⭕', '❌']}, Targets('123', fake.get))
    seed = store.prepare('seed', '123', '321', payload)
    store.finish(seed['token'], 'sent', {'url': URL, 'channel_id': '456', 'message_id': '777'})
    config = s.config_from_reference(store, '123', {'reference_draft_id': seed['token']}, Targets('123', fake.get))
    row = store.prepare_schedule('weekly', '123', '321', config, CLOCK-86400)
    store.activate(row['id'], '123', '321', 'isolated-test-approval', CLOCK-86400)
    yield fake, engine, workspace, store, config, row
    store.close()


def run(setup, **kwargs):
    fake, engine, workspace, store, _, _ = setup
    return s.dispatch(store, workspace, engine, get=fake.get, write=fake.write, clock=CLOCK, **kwargs)


def test_tuesday_noon_seoul_not_midnight_or_utc():
    config = dict(weekday=1, hour=12, minute=0)
    assert s.slot(config, CLOCK) == CLOCK
    assert s.slot(config, CLOCK-1) == CLOCK-7*86400
    assert s.next_run(config, CLOCK).startswith('2026-12-01T12:00:00+09:00')
    assert s.slot(config, CLOCK+6*86400) == CLOCK


def test_dispatch_new_agenda_mentions_reactions_receipt_and_no_duplicate(setup):
    fake, engine, workspace, store, config, row = setup
    assert run(setup)[0]['state'] == 'sent'
    assert [c[0] for c in fake.calls] == ['POST', 'PUT', 'PUT']
    payload = fake.calls[0][2]
    assert payload['content'].endswith('2. 기타의견')
    assert '예전 안건' not in payload['content']
    assert payload['content'].startswith('<@&555> 오늘 9시 30분')
    assert payload['allowed_mentions']['roles'] == ['555']
    assert payload['enforce_nonce'] is True
    assert run(setup) == []
    assert len(fake.calls) == 3
    assert '2026-11-24T12:00:00+09:00' in engine.prompts[0]
    assert ('법동', 'notion', 12) in workspace.queries
    assert store.status('123', CLOCK)['schedules'][0]['last_run']['state'] == 'sent'


def test_preview_real_generation_but_no_run_reservation_or_write(setup):
    fake, engine, _, store, _, _ = setup
    result = run(setup, dry_run=True)
    assert result[0]['sent'] is False and engine.prompts
    assert not fake.calls
    assert store.db.execute('SELECT count(*) FROM schedule_runs').fetchone()[0] == 0


@pytest.mark.parametrize('offset', [-1, 1801, 86400])
def test_only_due_window_not_early_late_or_next_day(setup, offset):
    fake, engine, workspace, store, _, _ = setup
    assert s.dispatch(store, workspace, engine, get=fake.get, write=fake.write, clock=CLOCK+offset) == []
    assert not fake.calls and not engine.prompts


def test_registration_after_today_no_backfill(setup):
    fake, engine, workspace, store, _, row = setup
    store.db.execute('UPDATE schedules SET activated=?', (CLOCK+60,)); store.db.commit()
    assert s.dispatch(store, workspace, engine, get=fake.get, write=fake.write, clock=CLOCK+120) == []
    assert not fake.calls


def test_uncertain_post_is_never_reposted(setup):
    fake, _, _, store, _, row = setup
    fake.fail_post = True
    assert 'error' in run(setup)[0]
    assert run(setup) == []
    assert len(fake.calls) == 1
    assert store.status('123', CLOCK)['schedules'][0]['last_run']['state'] == 'unknown'


def test_reaction_error_does_not_repeat_message(setup):
    fake, _, _, store, _, _ = setup
    fake.fail_reaction = True
    assert run(setup)[0]['state'] == 'sent'
    assert run(setup) == []
    receipt = json.loads(store.status('123', CLOCK)['schedules'][0]['last_run']['receipt'])
    assert receipt['reaction_errors'] == ['⭕', '❌']


def test_role_revoked_before_execution_blocks_generation_and_post(setup):
    fake, engine, _, _, _, _ = setup
    fake.members['123']['roles'] = []
    assert run(setup)[0]['error'] == 'AccessDenied'
    assert not fake.calls and not engine.prompts


def test_role_revoked_while_generating_blocks_post(setup):
    fake, engine, _, _, _, _ = setup
    engine.hook = lambda: fake.members['123'].update(roles=[])
    assert run(setup)[0]['error'] == 'AccessDenied'
    assert not fake.calls


def test_cancel_while_generating_blocks_post(setup):
    fake, engine, _, store, _, row = setup
    engine.hook = lambda: store.cancel(row['id'], '123')
    assert 'error' in run(setup)[0]
    assert not fake.calls


@pytest.mark.parametrize('change', [{'quote': '원문에 없는 인용입니다.'}, {'url': 'https://evil.example/'},
    {'title': '@everyone 회의'}, {'title': '기타의견'}, {'title': '첫줄\n둘째줄'}])
def test_invalid_agenda_provenance_or_injected_mention_no_post(setup, change):
    fake, engine, _, _, _, _ = setup
    engine.result['agenda'][0].update(change)
    assert run(setup)[0]['error'] == 'agenda_provenance_invalid'
    assert not fake.calls


def test_two_connections_only_one_claim_and_old_lease_cannot_post(setup):
    _, _, _, store, _, row = setup
    second = s.ScheduleStore(store.db.execute('PRAGMA database_list').fetchone()[2])
    try:
        lease = store.claim_run(row['id'], CLOCK, CLOCK)
        assert second.claim_run(row['id'], CLOCK, CLOCK) is None
        new = second.claim_run(row['id'], CLOCK, CLOCK+301)
        assert new and new != lease
        with pytest.raises(ValueError):
            store.reserve_post(row['id'], CLOCK, lease, {})
        second.reserve_post(row['id'], CLOCK, new, {})
        assert store.claim_run(row['id'], CLOCK, CLOCK+1000) is None
    finally:
        second.close()


def test_restart_preserves_active_schedule_and_send_receipt(setup):
    fake, engine, workspace, store, _, _ = setup
    run(setup)
    second = s.ScheduleStore(store.db.execute('PRAGMA database_list').fetchone()[2])
    try:
        assert s.dispatch(second, workspace, engine, get=fake.get, write=fake.write, clock=CLOCK) == []
        assert len(fake.calls) == 3
    finally:
        second.close()


def test_backup_includes_schedule_and_occurrence(setup, tmp_path):
    _, _, _, store, _, _ = setup
    run(setup)
    file = store.db.execute('PRAGMA database_list').fetchone()[2]
    backup = tmp_path/'backup.db'
    assert MessageStore.backup_existing(file, backup)
    copied = s.ScheduleStore(backup)
    try:
        assert copied.status()['schedules'][0]['last_run']['state'] == 'sent'
    finally:
        copied.close()


def test_model_cannot_activate_or_cancel_without_real_human_message(setup):
    fake, _, _, store, _, row = setup
    context = {'platform': 'discord', 'scope_id': s.GUILD_ID, 'actor_id': '123', 'chat_id': '321', 'thread_id': '', 'message_id': '789'}
    path = store.db.execute('PRAGMA database_list').fetchone()[2]
    for op in ['activate_schedule', 'cancel_schedule']:
        with pytest.raises(PermissionError):
            s.invoke({'op': op, 'data': {'schedule_id': row['id']}}, context=context, get=fake.get, path=path)
    fake.confirm = '예약중지 ' + row['id']
    assert s.invoke({'op': 'cancel_schedule', 'data': {'schedule_id': row['id']}}, context=context, get=fake.get, path=path)['result']['schedules'][0]['state'] == 'paused'


def test_same_binding_replace_instead_of_duplicate(setup):
    _, _, _, store, config, _ = setup
    newer = store.prepare_schedule('new-policy', '123', '321', config | {'policy': '새 정책'}, CLOCK-60)
    store.activate(newer['id'], '123', '321', 'isolated-new-approval', CLOCK-60)
    assert [r['state'] for r in store.records()].count('active') == 1
    with pytest.raises(ValueError):
        store.prepare_schedule('new-policy', '123', '321', config, CLOCK)


def test_tool_preview_then_actual_one_time_approval_and_repeat_activation(setup):
    fake, _, _, store, config, _ = setup
    context = {'platform': 'discord', 'scope_id': s.GUILD_ID, 'actor_id': '123', 'chat_id': '321', 'thread_id': '', 'message_id': '789'}
    path = store.db.execute('PRAGMA database_list').fetchone()[2]
    data = {'reference_draft_id': config['reference_draft_id'], 'policy': '매주 새로운 안건을 검토해요.'}
    params = {'op': 'prepare_schedule', 'request_id': 'human-weekly-request', 'data': data}
    preview = s.invoke(params, context=context, get=fake.get, path=path)['result']
    assert preview['preview']['hour'] == 12 and preview['preview']['timezone'] == 'Asia/Seoul'
    assert s.invoke(params, context=context, get=fake.get, path=path)['result']['schedule_id'] == preview['schedule_id']
    fake.confirm = preview['confirmation']
    activate = {'op': 'activate_schedule', 'data': {'schedule_id': preview['schedule_id']}}
    for _ in range(2):
        result = s.invoke(activate, context=context, get=fake.get, path=path)['result']
        assert sum(r['state'] == 'active' for r in result['schedules']) == 1
    assert not fake.calls


def test_expired_preview_and_other_actor_cannot_activate(setup):
    _, _, _, store, config, _ = setup
    preview = store.prepare_schedule('expired', '123', '321', config, CLOCK-901)
    with pytest.raises(ValueError):
        store.activate(preview['id'], '123', '321', 'test-expired', CLOCK)
    with pytest.raises(PermissionError):
        store.activate(preview['id'], '111', '321', 'test-spoof', CLOCK)


def test_fresh_notion_replaces_cache_and_failure_stays_explicit(setup):
    fake, _, workspace, _, config, _ = setup
    old = {'url': 'https://magical-sovereign-7b8.notion.site/abc', 'title': '법동 일정', 'content': '이전 일정이에요.'}
    workspace.search_documents = lambda query, kind, limit: [old] if kind == 'notion' else []
    docs = s.evidence(config, workspace, fake.get, lambda url: {'documents': [old | {'content': '이번 주 확인된 새 일정이에요.'}]})
    assert next(r for r in docs if r['url'] == old['url'])['content'] == '이번 주 확인된 새 일정이에요.'
    docs = s.evidence(config, workspace, fake.get, lambda url: {'documents': [], 'error': 'reader_timeout'})
    assert next(r for r in docs if r['url'] == old['url'])['live_read_failed'] is True


@pytest.mark.parametrize('during_generation', [False, True])
def test_requester_history_rights_separate_from_view_and_bot_rights(setup, during_generation):
    fake, engine, _, _, _, _ = setup
    def revoke():
        fake.channels['456']['permission_overwrites'] = [{'id': '123', 'type': 1, 'allow': '0', 'deny': str(1 << 16)}]
    if during_generation:
        engine.hook = revoke
    else:
        revoke()
    assert run(setup)[0]['error'] == 'AccessDenied'
    assert not fake.calls
    assert bool(engine.prompts) is during_generation
