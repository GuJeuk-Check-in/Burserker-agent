import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import pytest


def module(name):
    path = Path(__file__).parents[1] / 'services/gujeuk_pm' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


concurrency = module('concurrency')
patches = module('hermes_patches')


def event(ident='20', actor='42', text='두 번째 질문'):
    return NS(text=text, source=NS(platform=NS(value='discord'), scope_id=concurrency.GUILD,
        user_id=actor, chat_id='10', thread_id='10', chat_type='thread', message_id=ident,
        request_lane=None))


class Adapter:
    def __init__(self):
        self._active_sessions = {}

    def _event_session_key(self, item):
        source = item.source
        base = f'discord:thread:{source.chat_id}:{source.user_id}'
        return base + (':question:' + source.request_lane if source.request_lane else '')


def test_two_questions_prepare_concurrently_without_cancelling_first():
    async def run():
        adapter = Adapter()
        first, second = event('19'), event('20')
        base = adapter._event_session_key(first)
        interrupted = asyncio.Event()
        adapter._active_sessions[base] = interrupted
        assert concurrency.fork_busy_question(adapter, second)
        assert not interrupted.is_set()
        assert adapter._event_session_key(second) != base
        assert second.source.chat_id == first.source.chat_id
        assert second.source.thread_id == first.source.thread_id
        assert second.source.user_id == first.source.user_id
        entered, both_entered, release = [], asyncio.Event(), asyncio.Event()
        async def answer(item):
            entered.append(item.source.message_id)
            if len(entered) == 2:
                both_entered.set()
            await release.wait()
            return item.source.message_id
        tasks = [asyncio.create_task(answer(item)) for item in (first, second)]
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        release.set()
        assert await asyncio.gather(*tasks) == ['19', '20']
    asyncio.run(run())


@pytest.mark.parametrize('field,value', [('scope_id','other'),('chat_type','dm'),('message_id','invalid')])
def test_fork_fails_closed_outside_scope(field, value):
    adapter, item = Adapter(), event()
    adapter._active_sessions[adapter._event_session_key(item)] = asyncio.Event()
    setattr(item.source, field, value)
    assert not concurrency.fork_busy_question(adapter, item)


def test_commands_and_over_capacity_follow_normal_queue():
    adapter, item = Adapter(), event()
    key = adapter._event_session_key(item)
    adapter._active_sessions[key] = asyncio.Event()
    for i in range(concurrency.MAX_FORKS_PER_CONVERSATION):
        adapter._active_sessions[key + ':question:' + str(i)] = asyncio.Event()
    assert not concurrency.fork_busy_question(adapter, item)
    item.text = '/stop'
    assert not concurrency.fork_busy_question(adapter, item)


def test_upstream_mismatch_rejected():
    with pytest.raises(ValueError):
        patches.session_patch('unknown upstream')
    with pytest.raises(ValueError):
        patches.ingress_patch('unknown upstream')
