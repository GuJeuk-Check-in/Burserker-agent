"""Run inside the existing authorized Hermes container. Never prints credentials."""
import json
from importlib import import_module
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
get_env_value = import_module('hermes_cli.config').get_env_value

GUILD='1413450780068937790'

def main():
    token=get_env_value('DISCORD_BOT_TOKEN')
    if not token or any(c.isspace() for c in token):
        raise ValueError('missing_or_invalid_existing_bot_credential')
    def get(route):
        request=Request('https://discord.com/api/v10'+route,
            headers={'Authorization':'Bot '+token,'User-Agent':'GujeukPMSetup/1.0'})
        with urlopen(request,timeout=20) as response:
            return json.load(response)
    me=get('/users/@me')
    if not me.get('bot'):
        raise ValueError('bot_identity_required')
    guild=get('/guilds/'+GUILD)
    if guild.get('id')!=GUILD:
        raise ValueError('wrong_guild')
    target=Path('/opt/data/pm-discord-token')
    if target.is_symlink():
        raise ValueError('credential_symlink_rejected')
    if target.exists():
        import hmac
        if not hmac.compare_digest(target.read_text().strip(),token):
            raise ValueError('existing_collector_credential_differs')
    else:
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as stream:
            stream.write(token+'\n'); stream.flush(); os.fsync(stream.fileno())
        os.chown(target,1000,1000)
    metadata=target.stat()
    if metadata.st_uid!=1000 or metadata.st_mode & 0o777!=0o600:
        raise ValueError('credential_permissions_incorrect')
    print(json.dumps({'verified_bot_id':me['id'],'verified_bot_name':me.get('username'),
                      'guild':guild['id'],'credential_reused':True,'credential_mode':'0600'}))

try:
    main()
except HTTPError as error:
    print(json.dumps({'setup_error':'discord_http','status':error.code}))
    raise SystemExit(1) from None
except Exception as error:
    print(json.dumps({'setup_error':type(error).__name__}))
    raise SystemExit(1) from None
