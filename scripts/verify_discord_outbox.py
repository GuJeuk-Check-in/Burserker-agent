"""Owner-only installed outbox/Discord permission probe; GET only, no drafts or posts.

Pipe this checked-in file to docker exec -i --user hermes with HOME=/opt/data and
PYTHONPATH=/opt/data/workspace/gujeuk-pm/src using the pinned Hermes Python.
This checks bot permissions, not a simulated human's approval or live delivery.
"""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace


def main():
    from hermes_cli.config import load_env
    os.environ.update(load_env())
    from oijwef098234.gujeuk_ai.access import GUILD_ID, PM_ROLE_IDS, discord_get
    from oijwef098234.gujeuk_ai.messaging import Targets
    plugin = Path('/opt/data/plugins/gujeuk-pm/__init__.py')
    spec = importlib.util.spec_from_file_location('installed_pm_outbox_probe', plugin)
    if spec is None or spec.loader is None:
        raise ValueError('installed_plugin_unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tools = []
    module.register(SimpleNamespace(register_tool=lambda **kw: tools.append(kw)))
    ops = set(tools[0]['schema']['parameters']['properties']['op']['enum'])
    me = discord_get('/users/@me')
    roles = discord_get(f'/guilds/{GUILD_ID}/roles')
    targets = Targets(me['id'], discord_get)
    result = targets.list()
    channels = discord_get(f'/guilds/{GUILD_ID}/channels')
    checks = {'installed_tools': {'channels', 'prepare_message', 'send_message'} <= ops,
              'pm_roles_exist': PM_ROLE_IDS <= {str(r['id']) for r in roles},
              'bot_user': me.get('bot') is True,
              'has_writable_channels': bool(result['channels'])}
    print(json.dumps({'checks': checks, 'bot_writable_channels_and_active_threads': len(result['channels']),
        'server_channels_including_categories': len(channels), 'discord_posted': False,
        'scope': GUILD_ID, 'passed': all(checks.values())}))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
