"""No live Discord messages/credentials: exercise a fake REST boundary and real SQLite receipts."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import pytest
from oijwef098234.gujeuk_ai import access, messaging as m


class Discord:
    def __init__(self):
        self.roles = [{'id': access.GUILD_ID, 'permissions': str(m.VIEW | m.SEND | m.HISTORY | m.REACT | m.THREAD_SEND)},
                      {'id': next(iter(access.PM_ROLE_IDS)), 'permissions': '0'},
                      {'id': '555', 'permissions': '0', 'mentionable': True}]
        self.members = {'123': {'user': {'id': '123'}, 'roles': list(access.PM_ROLE_IDS)},
                        '999': {'user': {'id': '999', 'bot': True}, 'roles': []}}
        self.channels = {'456': {'id': '456', 'guild_id': access.GUILD_ID, 'type': 0, 'name': '공지'},
                         '321': {'id': '321', 'guild_id': access.GUILD_ID, 'type': 11, 'parent_id': '456', 'name': '요청'}}
        self.confirm = ''
        self.author = '123'
        self.calls = []
        self.content = ''
        self.fail_post = self.fail_reaction = self.fail_readback = False

    def get(self, path):
        if path == '/users/@me':
            return {'id': '999', 'bot': True}
        if path.endswith('/roles'):
            return copy.deepcopy(self.roles)
        if '/members/' in path:
            return copy.deepcopy(self.members[path.rsplit('/', 1)[1]])
        if path.endswith('/threads/active'):
            return {'threads': [self.channels['321']]}
        if path == f'/guilds/{access.GUILD_ID}/channels':
            return [self.channels['456']]
        if path == '/channels/321/messages/789':
            return {'id': '789', 'channel_id': '321', 'author': {'id': self.author}, 'content': self.confirm}
        if path.endswith('/messages/777'):
            if self.fail_readback:
                raise access.AccessDenied('unavailable')
            return {'id': '777', 'author': {'id': '999'}, 'content': self.content}
        if '/thread-members/' in path:
            return {'user_id': path.rsplit('/', 1)[1]}
        return copy.deepcopy(self.channels[path.rsplit('/', 1)[1]])

    def write(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == 'POST':
            if self.fail_post:
                raise TimeoutError('private HTTP details must not escape')
            if path.endswith('/threads'):
                self.content = payload['message']['content']
                return {'id': '654', 'message': {'id': '777'}}
            self.content = payload['content']
            return {'id': '777'}
        if self.fail_reaction:
            raise m.DeliveryError('discord_http_403')
        return {}


@pytest.fixture
def env(tmp_path):
    fake = Discord()
    context = {'platform': 'discord', 'scope_id': access.GUILD_ID, 'actor_id': '123',
               'chat_id': '321', 'thread_id': '', 'message_id': '789'}
    def invoke(op, data=None, request='request-1', **context_override):
        return m.invoke({'op': op, 'data': data or {}, 'request_id': request},
            context=context | context_override, get=fake.get, write=fake.write, path=tmp_path/'outbox.db')['result']
    return fake, invoke, tmp_path


def prepare(invoke, **extra):
    return invoke('prepare_message', {'channel_id': '456', 'content': '회의 안내예요.', **extra})


def test_preview_real_confirmation_send_readback_and_idempotent_receipt(env):
    fake, invoke, path = env
    draft = prepare(invoke, reactions=['⭕', '❌'])
    assert not fake.calls
    assert (path/'outbox.db').stat().st_mode & 0o777 == 0o600
    fake.confirm = draft['confirmation']
    sent = invoke('send_message', {'draft_id': draft['draft_id']})
    assert sent['url'] == f'https://discord.com/channels/{access.GUILD_ID}/456/777'
    assert sent['reaction_errors'] == []
    assert fake.calls[0][2]['enforce_nonce'] is True
    assert fake.calls[0][2]['allowed_mentions']['parse'] == []
    assert [c[0] for c in fake.calls] == ['POST', 'PUT', 'PUT']
    assert invoke('send_message', {'draft_id': draft['draft_id']})['reused_receipt']
    assert len(fake.calls) == 3


@pytest.mark.parametrize('bad', ['맞아 보내', '인용: 발송 {token}', '발송 {token} 바꿔서 보내', ''])
def test_model_cannot_confirm_or_use_quoted_approval(env, bad):
    fake, invoke, _ = env
    draft = prepare(invoke)
    fake.confirm = bad.format(token=draft['draft_id'])
    with pytest.raises(PermissionError):
        invoke('send_message', {'draft_id': draft['draft_id']})
    assert not fake.calls


def test_other_actor_chat_and_missing_gateway_anchor_cannot_confirm(env):
    fake, invoke, _ = env
    draft = prepare(invoke)
    fake.confirm = draft['confirmation']
    fake.author = '999'
    with pytest.raises(PermissionError):
        invoke('send_message', {'draft_id': draft['draft_id']})
    fake.author = '123'
    with pytest.raises(ValueError):
        invoke('send_message', {'draft_id': draft['draft_id']}, message_id='')
    with pytest.raises(ValueError):
        invoke('send_message', {'draft_id': draft['draft_id'], 'confirmed': True})
    assert not fake.calls


def test_role_removal_denies_confirmed_send(env):
    fake, invoke, _ = env
    draft = prepare(invoke)
    fake.confirm = draft['confirmation']
    fake.members['123']['roles'] = []
    with pytest.raises(PermissionError):
        invoke('send_message', {'draft_id': draft['draft_id']})
    assert not fake.calls


@pytest.mark.parametrize('change', ['guild', 'dm', 'category', 'actor_hidden', 'bot_cannot_send', 'archived'])
def test_target_scope_and_actual_permissions_fail_closed(env, change):
    fake, invoke, _ = env
    c = fake.channels['456']
    if change == 'guild': c['guild_id'] = '555'
    if change == 'dm': c.pop('guild_id'); c['type'] = 1
    if change == 'category': c['type'] = 4
    if change == 'actor_hidden':
        c['permission_overwrites'] = [{'id': '123', 'type': 1, 'deny': str(m.VIEW), 'allow': '0'}]
    if change == 'bot_cannot_send':
        c['permission_overwrites'] = [{'id': '999', 'type': 1, 'deny': str(m.SEND), 'allow': '0'}]
    if change == 'archived':
        c.update(type=11, parent_id='321', thread_metadata={'archived': True})
    with pytest.raises(PermissionError):
        prepare(invoke)
    assert not fake.calls


def test_draft_is_immutable_expires_and_same_request_reuses_preview(env, monkeypatch):
    fake, invoke, _ = env
    draft = prepare(invoke)
    assert prepare(invoke)['draft_id'] == draft['draft_id']
    with pytest.raises(ValueError, match='different_message'):
        prepare(invoke, content='확인하지 않은 변경')
    fake.confirm = draft['confirmation']
    now = time.time()
    monkeypatch.setattr(m.time, 'time', lambda: now + 901)
    with pytest.raises(m.DeliveryError, match='expired'):
        invoke('send_message', {'draft_id': draft['draft_id']})
    assert not fake.calls


@pytest.mark.parametrize('which', ['post', 'readback'])
def test_uncertain_delivery_is_never_automatically_retried(env, which):
    fake, invoke, _ = env
    draft = prepare(invoke)
    fake.confirm = draft['confirmation']
    setattr(fake, 'fail_' + which, True)
    for _ in range(2):
        with pytest.raises(m.DeliveryError, match='uncertain'):
            invoke('send_message', {'draft_id': draft['draft_id']})
    assert len(fake.calls) == 1


def test_reaction_failure_does_not_duplicate_post(env):
    fake, invoke, _ = env
    draft = prepare(invoke, reactions=['⭕'])
    fake.confirm = draft['confirmation']
    fake.fail_reaction = True
    receipt = invoke('send_message', {'draft_id': draft['draft_id']})
    assert receipt['reaction_errors'] == ['⭕']
    assert invoke('send_message', {'draft_id': draft['draft_id']})['message_id'] == '777'
    assert sum(c[0] == 'POST' for c in fake.calls) == 1


def test_mentions_explicit_and_everyone_disabled(env):
    fake, invoke, _ = env
    draft = prepare(invoke, content='<@&555> 참석 확인', mention_roles=['555'])
    assert draft['preview']['allowed_mentions']['roles'] == ['555']
    with pytest.raises(ValueError):
        prepare(invoke, content='@everyone 회의')
    with pytest.raises(ValueError):
        prepare(invoke, content='<@&555> 회의', mention_roles=[access.GUILD_ID])
    fake.roles[-1]['mentionable'] = False
    with pytest.raises(PermissionError):
        prepare(invoke, content='<@&555> 회의', mention_roles=['555'])
    assert not fake.calls


def test_forum_new_post_requires_title_and_uses_documented_endpoint(env):
    fake, invoke, _ = env
    fake.channels['456'].update(type=15, flags=16, available_tags=[{'id': '888'}])
    with pytest.raises(ValueError):
        prepare(invoke)
    draft = prepare(invoke, title='회의 안건', tags=['888'])
    fake.confirm = draft['confirmation']
    receipt = invoke('send_message', {'draft_id': draft['draft_id']})
    assert receipt['channel_id'] == '654'
    assert fake.calls[0][1] == '/channels/456/threads'
    assert 'nonce' not in fake.calls[0][2]['message']


def test_channels_include_all_permitted_targets_not_an_allowlist(env):
    fake, invoke, _ = env
    assert {r['id'] for r in invoke('channels')['channels']} == {'456', '321'}
    fake.channels['456']['permission_overwrites'] = [{'id': '123', 'type': 1, 'deny': str(m.VIEW), 'allow': '0'}]
    assert invoke('channels')['channels'] == []


def test_two_threads_cannot_claim_same_receipt(tmp_path):
    path = tmp_path/'outbox.db'
    store = m.MessageStore(path)
    row = store.prepare('request', '123', '321', {'content': 'x'})
    store.close()
    barrier = threading.Barrier(2)
    def claim():
        s = m.MessageStore(path)
        barrier.wait(timeout=5)
        try:
            s.claim(row['token'], '123', '321')
            return 'claimed'
        except m.DeliveryError:
            return 'denied'
        finally:
            s.close()
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: claim(), range(2))) == ['claimed', 'denied']


def test_receipt_bound_to_original_actor_and_conversation_and_backed_up(tmp_path):
    source, target = tmp_path/'outbox.db', tmp_path/'backup.db'
    store = m.MessageStore(source)
    draft = store.prepare('one', '123', '321', {'content': 'reviewed'})
    for actor, origin in [('999', '321'), ('123', '555')]:
        with pytest.raises(PermissionError):
            store.claim(draft['token'], actor, origin)
    assert m.MessageStore.backup_existing(source, target)
    store.close()
    backup = m.MessageStore(target)
    assert json.loads(backup.claim(draft['token'], '123', '321')['payload'])['content'] == 'reviewed'
    backup.close()
    assert target.stat().st_mode & 0o777 == 0o600
    assert not m.MessageStore.backup_existing(tmp_path/'missing.db', tmp_path/'unused.db')
    assert not (tmp_path/'unused.db').exists()


def test_channel_permission_revocation_after_preview_prevents_post(env):
    fake, invoke, _ = env
    draft = prepare(invoke)
    fake.confirm = draft['confirmation']
    fake.channels['456']['permission_overwrites'] = [
        {'id': '999', 'type': 1, 'deny': str(m.SEND), 'allow': '0'}]
    with pytest.raises(PermissionError):
        invoke('send_message', {'draft_id': draft['draft_id']})
    assert not fake.calls


def test_read_only_member_can_list_but_never_prepare(env):
    fake, invoke, path = env
    fake.members['123']['roles'] = []
    assert invoke('channels')['channels']
    with pytest.raises(PermissionError):
        prepare(invoke)
    assert not (path/'outbox.db').exists()


def test_no_redirect_can_carry_bot_token_to_another_host():
    with pytest.raises(m.DeliveryError, match='redirect_denied'):
        m.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://untrusted.invalid')
