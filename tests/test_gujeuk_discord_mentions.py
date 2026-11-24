import importlib.util
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any
import re

import pytest

spec=importlib.util.spec_from_file_location('discord_mention_patch',
    Path(__file__).parents[1]/'services/gujeuk_pm/discord_mentions.py')
assert spec is not None and spec.loader is not None
patch=importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def adapter(source=None) -> Any:
    source='class Adapter:\n'+(source or patch.NEW)
    namespace: dict[str, Any]={'Any':object}
    exec(compile(source,'<tested-mention-predicate>','exec'),namespace)
    obj=namespace['Adapter']()
    obj._client=NS(user=NS(id=1572127703036072029))
    obj._raw_mentioned_user_ids=lambda msg:set(re.findall(r'<@!?(\d+)>',msg.content))
    return obj


def message(content='', roles=(), users=()):
    return NS(content=content,role_mentions=list(roles),mentions=list(users))


def test_actual_seonwoo_managed_role_payload_is_recognized():
    role=NS(id=1572130273934573591,managed=True,tags=NS(bot_id=1572127703036072029))
    msg=message('<@&1572130273934573591> 구즉 하위방문목적 관련된 기능 명세서랑 api 명세서 확인해볼래?',roles=[role])
    assert not adapter(patch.OLD)._self_is_explicitly_mentioned(msg)
    assert adapter()._self_is_explicitly_mentioned(msg)


@pytest.mark.parametrize('role', [NS(managed=False,tags=NS(bot_id=1572127703036072029)),
    NS(managed=True,tags=NS(bot_id=999)),NS(managed=True,tags=None),NS(managed=False)])
def test_unrelated_roles_and_same_named_roles_do_not_wake_bot(role):
    assert not adapter()._self_is_explicitly_mentioned(message('<@&123>',roles=[role]))


def test_existing_user_mentions_and_nonmentions_unchanged():
    obj=adapter()
    assert obj._self_is_explicitly_mentioned(message('<@1572127703036072029> 질문'))
    assert obj._self_is_explicitly_mentioned(message('<@!1572127703036072029> 질문'))
    assert obj._self_is_explicitly_mentioned(message(users=[obj._client.user]))
    assert not obj._self_is_explicitly_mentioned(message('질문'))
    obj._client=None
    assert not obj._self_is_explicitly_mentioned(message('<@1572127703036072029>'))


def test_exact_patch_is_idempotent_and_preserves_raw_bot_to_bot_predicate():
    source=('from typing import Any\nclass Adapter:\n'+patch.OLD+
        '\n    def _self_is_raw_mentioned(self, message):\n        return False\n'+
        '    async def _handle_message(self, message):\n        """Dispatch the message."""\n        return True\n')
    updated=patch.patched_source(source)
    assert patch.patched_source(updated)==updated
    assert '    def _self_is_raw_mentioned(self, message):\n        return False\n' in updated
    assert updated.index(patch.ADMISSION_CALL) < updated.index('        return True\n', updated.index('async def _handle_message'))
    with pytest.raises(ValueError):patch.patched_source('upstream changed')


def test_install_backs_up_before_atomic_replacement(tmp_path):
    path=tmp_path/'adapter.py'
    original=('from typing import Any\nclass Adapter:\n'+patch.OLD+
        '    async def _handle_message(self, message):\n        """Dispatch."""\n        return True\n')
    path.write_text(original)
    path.chmod(0o640)
    backup=patch.install(path,tmp_path/'backups')
    assert backup.read_text()==original
    assert path.stat().st_mode & 0o777==0o640
    assert patch.NEW in path.read_text()
    assert patch.install(path,tmp_path/'backups') is None


class Thread:
    id = 42

    def __init__(self, previous=(), targets=None):
        self.previous = list(previous)
        self.targets = targets or {}
        self.history_calls = 0

    def history(self, *, limit, before):
        self.history_calls += 1
        assert limit == 1 and before is not None
        async def rows():
            for item in self.previous[:limit]:
                yield item
        return rows()

    async def fetch_message(self, ident):
        return self.targets[ident]


def gateway():
    source = ('from typing import Any\nclass Adapter:\n' + patch.OLD +
        '    async def _handle_message(self, message):\n'
        '        """Dispatch after admission."""\n'
        '        self.acknowledged.append(message.content)\n'
        '        return True\n')
    namespace = {'Any': object, 'discord': NS(Thread=Thread), 're': re}
    exec(compile(patch.patched_source(source), '<gateway-test>', 'exec'), namespace)
    obj = namespace['Adapter']()
    obj._client = NS(user=NS(id=1572127703036072029))
    obj._raw_mentioned_user_ids = lambda msg: set(re.findall(r'<@!?(\d+)>', msg.content))
    obj._discord_thread_require_mention = lambda: False
    obj._threads = {'42'}
    obj.acknowledged = []
    return obj


def thread_message(content, *, author=123, channel=None, reference=None, minutes=0, roles=(), users=()):
    return NS(content=content, author=NS(id=author, bot=False), channel=channel or Thread(),
        reference=reference, created_at=datetime(2026, 11, 24, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        role_mentions=list(roles), mentions=list(users))


def dispatch(obj, msg):
    return asyncio.run(obj._handle_message(msg))


def test_human_chitchat_is_dropped_before_acknowledgement():
    obj = gateway()
    bot = thread_message('답변', author=obj._client.user.id)
    channel = Thread([bot])
    assert not dispatch(obj, thread_message('정욱아 영어도 허용 좀 해줘', channel=channel, minutes=1))
    human = thread_message('정욱아 영어도 허용 좀 해줘', author=456, minutes=1)
    channel.previous = [human, bot]
    assert not dispatch(obj, thread_message('응 꼭 반영해놓을게', channel=channel, minutes=2))
    assert obj.acknowledged == []


def test_direct_bot_mentions_and_real_followup_are_admitted():
    obj = gateway()
    channel = Thread([thread_message('방금 답변', author=obj._client.user.id)])
    assert dispatch(obj, thread_message('그럼 영어도 지원해줘', channel=channel, minutes=1))
    assert dispatch(obj, thread_message('<@1572127703036072029> 확인해줘', channel=Thread()))
    assert dispatch(obj, thread_message('<@1572127703036072029> <@456> 확인해줘', channel=Thread()))
    assert dispatch(obj, thread_message('확인해줘', channel=Thread(), users=[obj._client.user]))
    role = NS(id=777, managed=True, tags=NS(bot_id=obj._client.user.id))
    assert dispatch(obj, thread_message('<@&777> 확인해줘', channel=Thread(), roles=[role]))
    assert len(obj.acknowledged) == 5


def test_other_mentions_and_human_reply_target_override_bot_history():
    obj = gateway()
    bot = thread_message('답변', author=obj._client.user.id)
    channel = Thread([bot])
    assert not dispatch(obj, thread_message('<@456> 이거 해줘', channel=channel, minutes=1))
    assert not dispatch(obj, thread_message('<@&888> 이거 해줘', channel=channel, minutes=1))
    assert not dispatch(obj, thread_message('이거 해줘', channel=channel, minutes=1,
        users=[NS(id=456)]))
    human = thread_message('질문', author=456)
    assert not dispatch(obj, thread_message('응', channel=channel, reference=NS(resolved=human), minutes=1,
        users=[NS(id=obj._client.user.id)]))
    assert dispatch(obj, thread_message('그 부분 더 알려줘', channel=channel,
        reference=NS(resolved=bot), minutes=1))
    assert not dispatch(obj, thread_message('그 부분 더 알려줘', channel=channel,
        reference=NS(resolved=bot), users=[NS(id=obj._client.user.id), NS(id=456)], minutes=1))
    assert obj.acknowledged == ['그 부분 더 알려줘']


def test_unresolved_reply_target_and_stale_or_missing_context_fail_closed():
    obj = gateway()
    bot = thread_message('답변', author=obj._client.user.id)
    channel = Thread([bot], {5: bot})
    assert dispatch(obj, thread_message('계속', channel=channel, reference=NS(resolved=None, message_id=5)))
    assert not dispatch(obj, thread_message('계속', channel=channel,
        reference=NS(resolved=None, message_id=6)))
    assert not dispatch(obj, thread_message('계속', channel=Thread([bot]), minutes=31))
    assert not dispatch(obj, thread_message('계속', channel=Thread()))


def test_scope_and_existing_mention_gate_stay_intact():
    obj = gateway()
    msg = thread_message('일반 메시지', channel=Thread())
    obj._threads = set()
    assert dispatch(obj, msg)
    obj._threads = {'42'}
    obj._discord_thread_require_mention = lambda: True
    assert dispatch(obj, msg)
