"""Discord team tool authorization, independent of model-provided arguments."""
from __future__ import annotations

import json
from urllib.parse import urlparse
from urllib.request import Request, urlopen

GUILD_ID = '1413450780068937790'
PM_ROLE_IDS = frozenset({'1573296642025595021', '1573296736544493619'})
READ_OPS = frozenset({'status', 'tasks', 'search', 'read_notion'})
WRITE_OPS = frozenset({'create', 'propose'})
DENIED = '업무 변경은 구즉 PM 또는 법동 PM 역할이 있는 분만 요청할 수 있어요.'


class AccessDenied(PermissionError):
    """Only sanitized, user-visible authorization failures."""


def discord_get(path):
    """Use the gateway's credential resolver; never return credentials or raw HTTP errors."""
    from agent.secret_scope import get_secret
    token = get_secret('DISCORD_BOT_TOKEN')
    if not token:
        raise AccessDenied('Discord 권한을 확인할 수 없어 요청을 거절했어요.')
    request = Request('https://discord.com/api/v10' + path,
                      headers={'Authorization': 'Bot ' + token, 'User-Agent': 'gujeuk-pm'})
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('response limit')
            return json.loads(raw)
    except Exception:
        raise AccessDenied('Discord 권한을 확인할 수 없어 요청을 거절했어요.') from None


def authorize(op, *, platform, guild_id, actor_id, get=discord_get):
    """Fail closed, rechecking membership/roles for every request, including retries.

    Actor/guild must originate in gateway ContextVars, never tool arguments. PM
    authority permits only workspace draft creation/proposals, not approval,
    deletion, Notion editing or arbitrary server operations.
    """
    if platform != 'discord' or guild_id != GUILD_ID or not actor_id or not actor_id.isdecimal():
        raise AccessDenied('이 도구는 구즉 서버의 팀 업무 질문에만 사용할 수 있어요.')
    if op not in READ_OPS | WRITE_OPS:
        raise AccessDenied('승인·삭제·서버 명령은 이 도구에서 실행할 수 없어요.')
    member = get(f'/guilds/{GUILD_ID}/members/{actor_id}')
    if not isinstance(member, dict) or str(member.get('user', {}).get('id', '')) != actor_id:
        raise AccessDenied('서버 구성원 권한을 확인할 수 없어요.')
    if member.get('user', {}).get('bot') or member.get('pending'):
        raise AccessDenied('팀 구성원만 사용할 수 있어요.')
    roles = frozenset(str(r) for r in member.get('roles', []))
    if op in WRITE_OPS and not roles & PM_ROLE_IDS:
        raise AccessDenied(DENIED)
    return roles


def authorize_session(op):
    from gateway.session_context import get_session_env
    return authorize(op, platform=get_session_env('HERMES_SESSION_PLATFORM'),
                     guild_id=get_session_env('HERMES_SESSION_SCOPE_ID'),
                     actor_id=get_session_env('HERMES_SESSION_ACTOR_ID'))


def filter_evidence(result, roles, actor_id, *, get=discord_get):
    """Do not expose collected Discord channels the requester cannot view.

    Search is bounded to ten documents. Unknown permissions and private threads
    fail closed. No cache survives a call, so role removal takes effect promptly.
    """
    documents = result.get('result', {}).get('documents')
    if documents is None:
        return result
    channels, role_rows = {}, None
    def visible(doc):
        nonlocal role_rows
        parsed = urlparse(doc.get('url', ''))
        if doc.get('kind') != 'discord' and parsed.hostname not in {'discord.com', 'www.discord.com', 'discordapp.com'}:
            return True
        parts = parsed.path.strip('/').split('/')
        if len(parts) != 4 or parts[:2] != ['channels', GUILD_ID] or not parts[2].isdecimal():
            return False
        channel_id = parts[2]
        if channel_id in channels:
            return channels[channel_id]
        try:
            channel = get('/channels/' + channel_id)
            if str(channel.get('guild_id')) != GUILD_ID:
                return False
            # Private thread membership is deliberately not inferred. Public
            # threads inherit their parent's View Channel permission.
            if channel.get('type') == 12:
                return False
            if channel.get('type') in {10, 11}:
                channel = get('/channels/' + str(channel['parent_id']))
                if str(channel.get('guild_id')) != GUILD_ID:
                    return False
            if role_rows is None:
                role_rows = get('/guilds/' + GUILD_ID + '/roles')
            held = roles | {GUILD_ID}
            permissions = 0
            for role in role_rows:
                if str(role['id']) in held:
                    permissions |= int(role['permissions'])
            if permissions & (1 << 3):
                channels[channel_id] = True
                return True
            overwrites = channel.get('permission_overwrites', [])
            def apply(perms, rows):
                deny, allow = 0, 0
                for row in rows:
                    deny |= int(row['deny']); allow |= int(row['allow'])
                return (perms & ~deny) | allow
            permissions = apply(permissions, [r for r in overwrites if str(r['id']) == GUILD_ID])
            permissions = apply(permissions, [r for r in overwrites if r['type'] == 0 and str(r['id']) in roles])
            permissions = apply(permissions, [r for r in overwrites if r['type'] == 1 and str(r['id']) == actor_id])
            history_permissions = (1 << 10) | (1 << 16)
            channels[channel_id] = permissions & history_permissions == history_permissions
            return channels[channel_id]
        except Exception:
            channels[channel_id] = False
            return False
    allowed = [doc for doc in documents if visible(doc)]
    result['result']['documents'] = allowed
    result['result']['access_filtered'] = len(allowed) != len(documents)
    return result


def filter_session_evidence(result, roles):
    from gateway.session_context import get_session_env
    return filter_evidence(result, roles, get_session_env('HERMES_SESSION_ACTOR_ID'))
