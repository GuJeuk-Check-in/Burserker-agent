#!/usr/bin/env python3
"""Interactive Discord credential enrollment; never print or accept tokens in argv."""
import getpass
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request

BASE = Path(__file__).resolve().parent
EXPECTED_BOT_ID = '1572127703036072029'
OWNER_ID = '860711233238204436'
GUILD_ID = '1413450780068937790'
CHANNEL_ID = '1572130653506371645'


def discord_get(token, route):
    request = urllib.request.Request(
        'https://discord.com/api/v10' + route,
        headers={'Authorization': 'Bot ' + token, 'User-Agent': 'GujeukPMSetup/1.0'},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def render_env(token):
    if not token or any(char.isspace() for char in token):
        raise ValueError('토큰은 공백이나 줄바꿈 없이 입력해 주세요.')
    if not all(char.isalnum() or char in '._-' for char in token):
        raise ValueError('토큰 형식을 확인해 주세요.')
    values = {
        'DISCORD_BOT_TOKEN': token,
        'DISCORD_ALLOWED_USERS': OWNER_ID,
        'DISCORD_HOME_CHANNEL': CHANNEL_ID,
        'DISCORD_HOME_CHANNEL_NAME': 'gujeuk-pm-private',
        'DISCORD_REQUIRE_MENTION': 'true',
        'DISCORD_ALLOW_ALL_USERS': 'false',
        'GATEWAY_ALLOW_ALL_USERS': 'false',
        'DISCORD_ALLOW_BOTS': 'none',
        'DISCORD_AUTO_THREAD': 'true',
        'API_SERVER_ENABLED': 'false',
    }
    return '\n'.join(key + '=' + value for key, value in values.items()) + '\n'


def main():
    os.umask(0o077)
    target = BASE / 'data' / '.env'
    if target.is_symlink():
        raise SystemExit('설정 파일이 심볼릭 링크여서 중단했어요.')
    # The official entrypoint generates a loopback API key even before login.
    # Preserve unrelated settings and never print the existing file.
    original = target.read_text() if target.exists() else None
    existing = re.search(r'(?m)^DISCORD_BOT_TOKEN=(.*)$', original or '')
    token = existing.group(1).strip().strip('\"\'') if existing else ''
    if token:
        print('기존 Discord 토큰으로 연결 정보를 다시 검증해요. 값은 표시하지 않아요.')
    else:
        print('Discord Developer Portal → Bot → Reset Token에서 받은 토큰을 입력하세요.')
        print('입력은 화면에 표시되지 않고 셸 기록에도 남지 않아요.')
        token = getpass.getpass('Discord 봇 토큰: ').strip()
    try:
        content = render_env(token)
        bot = discord_get(token, '/users/@me')
        if str(bot.get('id')) != EXPECTED_BOT_ID or not bot.get('bot'):
            raise ValueError('전달받은 Application ID와 다른 봇이에요. 저장하지 않았어요.')
        guild = discord_get(token, '/guilds/' + GUILD_ID)
        channel = discord_get(token, '/channels/' + CHANNEL_ID)
        if str(channel.get('guild_id')) != GUILD_ID:
            raise ValueError('보고 채널이 지정한 서버에 속하지 않아요. 저장하지 않았어요.')
    except urllib.error.HTTPError as error:
        raise SystemExit(f'Discord 확인 실패: HTTP {error.code}. 토큰·서버 초대·채널 권한을 확인해 주세요.') from None
    except (urllib.error.URLError, TimeoutError):
        raise SystemExit('Discord 네트워크 확인에 실패했어요. 토큰은 저장하지 않았어요.') from None
    except ValueError as error:
        raise SystemExit(str(error)) from None
    target.parent.mkdir(mode=0o700, exist_ok=True)
    managed = {line.split('=', 1)[0] for line in content.splitlines()}
    preserved = []
    for line in (original or '').splitlines():
        match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
        if not match or match.group(1) not in managed:
            preserved.append(line)
    content = '\n'.join(preserved).rstrip() + '\n' + content
    current = target.read_text() if target.exists() else None
    if current != original or target.is_symlink():
        raise SystemExit('설정 파일이 작업 중 변경됐어요. 저장하지 않았어요.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f'봇 ID 확인: {bot["id"]}')
    print(f'서버 ID 확인: {guild["id"]}')
    print(f'보고 채널 ID 확인: {channel["id"]}')
    print('토큰을 소유자 전용 파일에 저장했어요. 아직 메시지를 보내거나 게이트웨이를 시작하지 않았어요.')


if __name__ == '__main__':
    main()
