"""Guild-only, PM-authorized Discord outbox. A real follow-up confirms an immutable draft."""
from __future__ import annotations
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .access import AccessDenied, GUILD_ID, authorize, discord_get

OPS = frozenset({'channels', 'prepare_message', 'send_message'})
TEXT_TYPES = frozenset({0, 2, 5, 10, 11, 12, 13, 15})
EMOJI = frozenset({'⭕', '❌', '✅', '👍', '👎'})
VIEW, SEND, HISTORY, REACT, THREAD_SEND = 1 << 10, 1 << 11, 1 << 16, 1 << 6, 1 << 38


class DeliveryError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise DeliveryError('redirect_denied')


def discord_write(method, path, payload=None):
    from agent.secret_scope import get_secret
    token = get_secret('DISCORD_BOT_TOKEN')
    if not token:
        raise DeliveryError('credential_unavailable')
    body = None if payload is None else json.dumps(payload).encode()
    request = Request('https://discord.com/api/v10' + path, data=body, method=method,
        headers={'Authorization': 'Bot ' + token, 'Content-Type': 'application/json', 'User-Agent': 'gujeuk-pm'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise DeliveryError('response_limit')
        return json.loads(raw) if raw else {}
    except HTTPError as error:
        raise DeliveryError('discord_http_' + str(error.code)) from None
    except DeliveryError:
        raise
    except Exception:
        raise DeliveryError('delivery_outcome_unknown') from None


def snowflake(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,22}', value):
        raise ValueError('invalid_discord_id')
    return value


def permissions(channel, member, roles):
    held = frozenset(str(r) for r in member.get('roles', [])) | {GUILD_ID}
    result = 0
    for role in roles:
        if str(role['id']) in held:
            result |= int(role['permissions'])
    if result & (1 << 3):
        return (1 << 53) - 1
    rows = channel.get('permission_overwrites', [])
    groups = ([r for r in rows if str(r['id']) == GUILD_ID],
        [r for r in rows if r['type'] == 0 and str(r['id']) in held - {GUILD_ID}],
        [r for r in rows if r['type'] == 1 and str(r['id']) == str(member['user']['id'])])
    for group in groups:
        deny, allow = 0, 0
        for row in group:
            deny |= int(row['deny']); allow |= int(row['allow'])
        result = (result & ~deny) | allow
    return result


class Targets:
    def __init__(self, actor, get):
        self.actor, self.get = actor, get
        self.member = get(f'/guilds/{GUILD_ID}/members/{actor}')
        self.me = get('/users/@me')
        self.bot = get(f'/guilds/{GUILD_ID}/members/{snowflake(self.me["id"])}')
        self.roles = get(f'/guilds/{GUILD_ID}/roles')

    def check(self, ident, *, reactions=False):
        channel = self.get('/channels/' + snowflake(ident))
        if str(channel.get('id')) != ident or str(channel.get('guild_id')) != GUILD_ID:
            raise AccessDenied('다른 서버나 DM에는 보낼 수 없어요.')
        kind = channel.get('type')
        if kind not in TEXT_TYPES:
            raise AccessDenied('이 채널은 텍스트 메시지 전송 대상이 아니에요.')
        parent = channel
        if kind in {10, 11, 12}:
            if channel.get('thread_metadata', {}).get('archived') or channel.get('thread_metadata', {}).get('locked'):
                raise AccessDenied('보관·잠긴 스레드는 변경하지 않아요.')
            parent = self.get('/channels/' + snowflake(channel['parent_id']))
            if str(parent.get('guild_id')) != GUILD_ID:
                raise AccessDenied('스레드 서버가 일치하지 않아요.')
            if kind == 12:
                for user in (self.actor, self.me['id']):
                    row = self.get(f'/channels/{ident}/thread-members/{snowflake(user)}')
                    if str(row.get('user_id')) != user:
                        raise AccessDenied('비공개 스레드 참여를 확인할 수 없어요.')
        if not permissions(parent, self.member, self.roles) & VIEW:
            raise AccessDenied('요청자가 볼 수 없는 채널에는 보낼 수 없어요.')
        needed = VIEW | HISTORY | (THREAD_SEND if kind in {10, 11, 12} else SEND)
        if reactions:
            needed |= REACT
        bot_perms = permissions(parent, self.bot, self.roles)
        if bot_perms & needed != needed:
            raise AccessDenied('봇의 채널 보기·전송·기록 보기·반응 권한을 확인해 주세요.')
        return channel, bot_perms

    def list(self):
        channels = self.get(f'/guilds/{GUILD_ID}/channels')
        threads = self.get(f'/guilds/{GUILD_ID}/threads/active').get('threads', [])
        result = []
        for row in channels + threads:
            if row.get('type') not in TEXT_TYPES:
                continue
            try:
                channel, _ = self.check(str(row['id']))
            except PermissionError:
                continue
            result.append({'id': channel['id'], 'name': channel.get('name'), 'type': channel['type'],
                           'requires_title': channel['type'] == 15})
        return {'channels': result, 'scope': GUILD_ID,
            'limits': '전체 서버/활성 스레드 중 요청자 열람·봇 전송 권한이 확인된 대상이에요. 카테고리·첨부 전용 미디어는 제외해요. 보관 스레드는 열지 않아요.'}


def message_data(data, targets):
    if not isinstance(data, dict) or set(data) - {'channel_id', 'content', 'reactions', 'mention_roles', 'mention_users', 'title', 'tags'}:
        raise ValueError('invalid_message_fields')
    ident = snowflake(data.get('channel_id'))
    text = data.get('content')
    if not isinstance(text, str) or not text.strip() or len(text.encode('utf-16-le')) // 2 > 2000:
        raise ValueError('message_must_be_1_to_2000_characters')
    if '@everyone' in text or '@here' in text:
        raise ValueError('everyone_here_mentions_not_enabled')
    reactions = data.get('reactions', [])
    if not isinstance(reactions, list) or len(reactions) > 5 or any(r not in EMOJI for r in reactions):
        raise ValueError('unsupported_reactions')
    channel, bot_perms = targets.check(ident, reactions=bool(reactions))
    mentions = {'parse': [], 'users': [], 'roles': [], 'replied_user': False}
    for key in ('roles', 'users'):
        values = data.get('mention_' + key, [])
        if not isinstance(values, list) or len(values) > 5:
            raise ValueError('mention_limit')
        for value in values:
            snowflake(value)
            if key == 'roles':
                role = next((r for r in targets.roles if str(r['id']) == value), None)
                if value == GUILD_ID or role is None or f'<@&{value}>' not in text:
                    raise ValueError('invalid_role_mention')
                if not role.get('mentionable') and not bot_perms & (1 << 17):
                    raise AccessDenied('이 역할 멘션을 허용하는 Discord 권한이 없어요.')
            else:
                member = targets.get(f'/guilds/{GUILD_ID}/members/{value}')
                if str(member.get('user', {}).get('id')) != value or not any(m in text for m in (f'<@{value}>', f'<@!{value}>')):
                    raise ValueError('invalid_member_mention')
            mentions[key].append(value)
    title, tags = data.get('title', ''), data.get('tags', [])
    if not isinstance(title, str) or not isinstance(tags, list) or len(tags) > 5:
        raise ValueError('invalid_forum_fields')
    if channel['type'] == 15:
        if not 1 <= len(title) <= 100:
            raise ValueError('forum_title_required')
        available = {str(t['id']) for t in channel.get('available_tags', [])}
        if any(t not in available for t in tags) or (channel.get('flags', 0) & 16 and not tags):
            raise ValueError('forum_tags_required_or_invalid')
    elif title or tags:
        raise ValueError('title_tags_only_for_forums')
    return {'channel_id': ident, 'content': text, 'reactions': list(dict.fromkeys(reactions)),
            'allowed_mentions': mentions, 'title': title, 'tags': tags, 'forum': channel['type'] == 15}


class MessageStore:
    def __init__(self, path):
        path = Path(path)
        if not path.parent.is_dir() or any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError('private_outbox_path_required')
        self.db = sqlite3.connect(path, timeout=15)
        self.db.row_factory = sqlite3.Row
        path.chmod(0o600)
        self.db.execute('''CREATE TABLE IF NOT EXISTS outbox(
            token TEXT PRIMARY KEY, request TEXT UNIQUE, actor TEXT, origin TEXT, payload TEXT,
            created REAL, state TEXT, receipt TEXT)''')
        self.db.commit()

    def prepare(self, request, actor, origin, payload):
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        key = json.dumps([actor, origin, request])
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO outbox VALUES(?,?,?,?,?,?,?,?)',
                (secrets.token_hex(8), key, actor, origin, raw, time.time(), 'prepared', None))
            row = self.db.execute('SELECT * FROM outbox WHERE request=?', (key,)).fetchone()
            if row['payload'] != raw:
                raise ValueError('request_id_reused_with_different_message')
        return dict(row)

    def claim(self, token, actor, origin):
        with self.db:
            row = self.db.execute('SELECT * FROM outbox WHERE token=? AND actor=? AND origin=?',
                                  (token, actor, origin)).fetchone()
            if row is None:
                raise AccessDenied('본인이 이 대화에서 만든 초안만 발송할 수 있어요.')
            if row['state'] == 'sent':
                return dict(row)
            if row['state'] != 'prepared':
                raise DeliveryError('delivery_outcome_uncertain_do_not_resend')
            if time.time() - row['created'] > 900:
                raise DeliveryError('draft_expired_prepare_again')
            changed = self.db.execute("UPDATE outbox SET state='sending' WHERE token=? AND state='prepared'", (token,))
            if changed.rowcount != 1:
                raise DeliveryError('delivery_already_in_progress')
        return dict(row)

    def finish(self, token, state, receipt=None):
        with self.db:
            self.db.execute('UPDATE outbox SET state=?,receipt=? WHERE token=?',
                            (state, json.dumps(receipt) if receipt else None, token))

    def close(self):
        self.db.close()

    @staticmethod
    def backup_existing(path, target):
        path, target = Path(path), Path(target)
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError('private_outbox_path_required')
        if not path.is_file():
            return False
        target.touch(mode=0o600, exist_ok=False)
        source = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        output = sqlite3.connect(target)
        try:
            source.backup(output)
            if output.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('outbox_backup_invalid')
        finally:
            source.close()
            output.close()
        return True


def invoke(params, *, context=None, get=discord_get, write=discord_write,
           path='/opt/data/pm-workspace/discord-outbox.db'):
    if context is None:
        from gateway.session_context import get_session_env
        context = {k: get_session_env('HERMES_SESSION_' + k.upper())
                   for k in ('platform', 'scope_id', 'actor_id', 'chat_id', 'thread_id', 'message_id')}
    op = params['op']
    authorize(op, platform=context['platform'], guild_id=context['scope_id'], actor_id=context['actor_id'], get=get)
    actor = context['actor_id']
    origin = snowflake(context.get('thread_id') or context['chat_id'])
    targets = Targets(actor, get)
    if op == 'channels':
        return {'ok': True, 'result': targets.list()}
    store = MessageStore(path)
    try:
        data = params.get('data', {})
        if op == 'prepare_message':
            request = params.get('request_id')
            if not isinstance(request, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request):
                raise ValueError('request_id_required')
            payload = message_data(data, targets)
            row = store.prepare(request, actor, origin, payload)
            return {'ok': True, 'result': {'draft_id': row['token'], 'preview': payload,
                'confirmation': '발송 ' + row['token'], 'expires_seconds': 900,
                'instruction': '대상·본문·멘션·반응을 그대로 사용자에게 보여주고, 사용자가 이 대화에 confirmation을 단독으로 입력할 때만 send_message를 호출하세요.'}}
        if op != 'send_message' or not isinstance(data, dict) or set(data) != {'draft_id'}:
            raise ValueError('send_requires_only_draft_id')
        token = data['draft_id']
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{16}', token):
            raise ValueError('invalid_draft_id')
        message = get(f'/channels/{origin}/messages/{snowflake(context.get("message_id"))}')
        if (str(message.get('id')) != context['message_id'] or str(message.get('channel_id')) != origin
                or str(message.get('author', {}).get('id')) != actor or message.get('author', {}).get('bot')
                or message.get('content', '').strip() != '발송 ' + token):
            raise AccessDenied('사용자가 이 대화에 확인 문구를 직접 입력해야 발송할 수 있어요.')
        row = store.claim(token, actor, origin)
        if row['state'] == 'sent':
            return {'ok': True, 'result': json.loads(row['receipt']) | {'reused_receipt': True}}
        payload = json.loads(row['payload'])
        try:
            # Recheck current roles/channel/mentions after human confirmation, not just preview time.
            authorize(op, platform=context['platform'], guild_id=context['scope_id'], actor_id=actor, get=get)
            message_data({'channel_id': payload['channel_id'], 'content': payload['content'],
                'reactions': payload['reactions'], 'mention_roles': payload['allowed_mentions']['roles'],
                'mention_users': payload['allowed_mentions']['users'], 'title': payload['title'], 'tags': payload['tags']}, targets)
        except Exception:
            store.finish(token, 'prepared')
            raise
        receipt = deliver(payload, token, targets, get=get, write=write,
                          finish=lambda state, receipt=None: store.finish(token, state, receipt))
        return {'ok': True, 'result': receipt}
    finally:
        store.close()


def deliver(payload, token, targets, *, get, write, finish):
    """Internal transport after caller's persistent reservation and authorization."""
    ident = payload['channel_id']
    body = {'content': payload['content'], 'allowed_mentions': payload['allowed_mentions']}
    try:
        if payload['forum']:
            response = write('POST', f'/channels/{ident}/threads',
                             {'name': payload['title'], 'message': body, 'applied_tags': payload['tags']})
            ident, sent = snowflake(response['id']), response['message']
        else:
            sent = write('POST', f'/channels/{ident}/messages', body | {'nonce': token, 'enforce_nonce': True})
        message_id = snowflake(sent['id'])
        confirmed = get(f'/channels/{ident}/messages/{message_id}')
        if (str(confirmed.get('author', {}).get('id')) != targets.me['id'] or confirmed.get('content') != payload['content']):
            raise DeliveryError('readback_not_confirmed')
        receipt = {'message_id': message_id, 'channel_id': ident,
            'url': f'https://discord.com/channels/{GUILD_ID}/{ident}/{message_id}', 'reaction_errors': []}
    except Exception:
        finish('unknown')
        raise DeliveryError('delivery_outcome_uncertain_do_not_resend') from None
    finish('sent', receipt)
    for emoji in payload['reactions']:
        try:
            write('PUT', f'/channels/{ident}/messages/{message_id}/reactions/{quote(emoji, safe="")}/@me')
        except Exception:
            receipt['reaction_errors'].append(emoji)
    finish('sent', receipt)
    return receipt
