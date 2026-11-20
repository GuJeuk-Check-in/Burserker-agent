import importlib.util
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
    source='from typing import Any\nclass Adapter:\n'+patch.OLD+'\n    def _self_is_raw_mentioned(self, message):\n        return False\n'
    updated=patch.patched_source(source)
    assert patch.patched_source(updated)==updated
    assert updated.endswith('    def _self_is_raw_mentioned(self, message):\n        return False\n')
    with pytest.raises(ValueError):patch.patched_source('upstream changed')


def test_install_backs_up_before_atomic_replacement(tmp_path):
    path=tmp_path/'adapter.py'
    original='from typing import Any\nclass Adapter:\n'+patch.OLD
    path.write_text(original)
    path.chmod(0o640)
    backup=patch.install(path,tmp_path/'backups')
    assert backup.read_text()==original
    assert path.stat().st_mode & 0o777==0o640
    assert patch.NEW in path.read_text()
    assert patch.install(path,tmp_path/'backups') is None
