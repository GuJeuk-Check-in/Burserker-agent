import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from oijwef098234.gujeuk_ai import access, bridge


def member(actor='123', roles=()):
    return {'user': {'id': actor}, 'roles': list(roles)}


@pytest.mark.parametrize('op', sorted(access.READ_OPS))
def test_members_may_only_read(op):
    assert access.authorize(op, platform='discord', guild_id=access.GUILD_ID,
                            actor_id='123', get=lambda _: member()) == frozenset()


@pytest.mark.parametrize('op', sorted(access.WRITE_OPS))
def test_non_pm_changes_denied(op):
    with pytest.raises(PermissionError, match='구즉 PM'):
        access.authorize(op, platform='discord', guild_id=access.GUILD_ID,
                         actor_id='123', get=lambda _: member(roles=['unrelated']))


@pytest.mark.parametrize('role', sorted(access.PM_ROLE_IDS))
@pytest.mark.parametrize('op', sorted(access.WRITE_OPS))
def test_exact_pm_roles_allow_only_drafts_and_proposals(role, op):
    assert role in access.authorize(op, platform='discord', guild_id=access.GUILD_ID,
                                    actor_id='123', get=lambda _: member(roles=[role]))


@pytest.mark.parametrize('platform,guild,actor', [('cli',access.GUILD_ID,'123'),
    ('discord','other','123'),('discord','','123'),('discord',access.GUILD_ID,''),
    ('discord',access.GUILD_ID,'../../123')])
def test_missing_context_other_guild_and_dm_fail_before_rest(platform, guild, actor):
    with pytest.raises(PermissionError):
        access.authorize('status', platform=platform, guild_id=guild, actor_id=actor,
                         get=lambda _: pytest.fail('must not call REST'))


@pytest.mark.parametrize('op', ['resolve', 'delete', 'shell', 'approve'])
def test_even_pm_cannot_approve_delete_or_run_shell(op):
    with pytest.raises(PermissionError):
        access.authorize(op, platform='discord', guild_id=access.GUILD_ID,
                         actor_id='123', get=lambda _: member(roles=access.PM_ROLE_IDS))


def test_membership_is_live_and_role_removal_revokes_retries():
    rows=iter([member(roles=access.PM_ROLE_IDS),member()])
    args=dict(platform='discord',guild_id=access.GUILD_ID,actor_id='123',get=lambda _: next(rows))
    access.authorize('create',**args)
    with pytest.raises(PermissionError):access.authorize('create',**args)
    with pytest.raises(PermissionError):
        access.authorize('tasks',platform='discord',guild_id=access.GUILD_ID,
                         actor_id='123',get=lambda _: member('999',access.PM_ROLE_IDS))


def test_plugin_checks_before_invoking_writer(monkeypatch):
    spec=importlib.util.spec_from_file_location('pm_role_test',
        Path(__file__).parents[1]/'services/gujeuk_pm/plugin/__init__.py')
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registered=[]
    module.register(SimpleNamespace(register_tool=lambda **kw:registered.append(kw)))
    def deny(op):raise access.AccessDenied(access.DENIED)
    monkeypatch.setattr(access,'authorize_session',deny)
    monkeypatch.setattr(bridge,'invoke',lambda _:pytest.fail('writer must not run'))
    response=json.loads(registered[0]['handler']({'op':'create','request_id':'spoof',
        'data':{'actor_id':'pm','roles':list(access.PM_ROLE_IDS)}}))
    assert response['error']=='permission_denied'
    assert access.DENIED in response['message']


@pytest.mark.parametrize('overwrites,roles,expected', [
    ([],frozenset(),True),
    ([{'id':access.GUILD_ID,'type':0,'deny':str(1<<10),'allow':'0'}],frozenset(),False),
    ([{'id':access.GUILD_ID,'type':0,'deny':str(1<<10),'allow':'0'},
      {'id':'pm','type':0,'deny':'0','allow':str(1<<10)}],frozenset({'pm'}),True),
    ([{'id':'123','type':1,'deny':str(1<<10),'allow':'0'}],frozenset({'pm'}),False),
])
def test_discord_evidence_respects_channel_overwrites(overwrites,roles,expected):
    def get(path):
        if path.endswith('/roles'):
            return [{'id':access.GUILD_ID,'permissions':str((1<<10)|(1<<16))}, {'id':'pm','permissions':'0'}]
        return {'guild_id':access.GUILD_ID,'type':0,'permission_overwrites':overwrites}
    data={'result':{'documents':[{'kind':'discord',
        'url':f'https://discord.com/channels/{access.GUILD_ID}/456/789','content':'private'}]}}
    result=access.filter_evidence(data,roles,'123',get=get)
    assert bool(result['result']['documents']) is expected


@pytest.mark.parametrize('channel_type,expected',[(11,True),(12,False)])
def test_public_threads_inherit_parent_but_private_threads_are_excluded(channel_type,expected):
    def get(path):
        if path.endswith('/roles'):return [{'id':access.GUILD_ID,'permissions':str((1<<10)|(1<<16))}]
        if path.endswith('/456'):return {'guild_id':access.GUILD_ID,'type':channel_type,'parent_id':'999'}
        return {'guild_id':access.GUILD_ID,'type':0}
    data={'result':{'documents':[{'kind':'discord','url':f'https://discord.com/channels/{access.GUILD_ID}/456/789'}]}}
    assert bool(access.filter_evidence(data,frozenset(),'123',get=get)['result']['documents']) is expected


def test_permission_check_errors_never_expose_evidence():
    def get(_):raise RuntimeError('private HTTP details')
    data={'result':{'documents':[{'kind':'discord','url':f'https://discord.com/channels/{access.GUILD_ID}/456/789'}]}}
    assert access.filter_evidence(data,frozenset(),'123',get=get)['result']['documents']==[]
