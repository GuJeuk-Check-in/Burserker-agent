"""Offline collector acceptance tests: no Discord, model, or production access."""
import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

spec = importlib.util.spec_from_file_location(
    "pm_collector", Path(__file__).parents[1] / "services/gujeuk_pm/collector.py")
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@pytest.fixture
def store(tmp_path):
    value = mod.Store(tmp_path / "state", "1")
    yield value
    value.db.close()


def channel(ident="10", kind=0, guild="1"):
    return {"id": ident, "guild_id": guild, "type": kind, "name": "test"}


def message(ident, chan="10", content="업무 자료"):
    return {"id": str(ident), "channel_id": chan, "author": {"id": "9"},
            "timestamp": "2026-11-17T00:00:00Z", "content": content}


class FakeAPI:
    def __init__(self, store, count=3):
        self.calls = []
        self.channels = [channel()]
        self.active = []
        self.messages = {"10": [message(store.initial_after() + i + 1) for i in range(count)]}
        self.fail = set()
        self.archive = {}

    def get(self, path, **params):
        self.calls.append((path, params))
        if path in self.fail:
            raise mod.APIError(403)
        if path == "/guilds/1/channels":
            return self.channels
        if path == "/guilds/1/threads/active":
            return {"threads": self.active}
        if path.endswith("/messages"):
            ident = path.split("/")[2]
            rows = self.messages.get(ident, [])
            if "after" in params:
                selected = [m for m in rows if int(m["id"]) > int(params["after"])]
                return list(reversed(selected[:100]))
            return list(reversed(rows[-100:]))
        if "/archived/" in path:
            return self.archive.get(path, {"threads": [], "has_more": False})
        raise AssertionError(path)


def test_pagination_duplicates_and_restart(store):
    api = FakeAPI(store, 225)
    result = mod.Collector(api, store).tick()
    assert result["messages"] == 225
    assert result["channels"][0]["status"] == "caught_up"
    cursor = store.cursor("10")
    mod.Collector(api, store).tick()
    assert store.status()["messages"] == 225
    reopened = mod.Store(store.directory, "1")
    assert reopened.cursor("10") == cursor
    assert reopened.status()["messages"] == 225
    reopened.db.close()


def test_bounded_catchup_resumes_without_recent_advancing_cursor(store):
    api = FakeAPI(store, 250)
    mod.Collector(api, store, max_pages=1).tick()
    assert store.cursor("10") == str(store.initial_after() + 100)
    assert store.status()["channels"][0]["status"] == "catching_up"
    mod.Collector(api, store, max_pages=1).tick()
    assert store.cursor("10") == str(store.initial_after() + 200)
    mod.Collector(api, store, max_pages=1).tick()
    assert store.status()["messages"] == 250
    assert store.status()["channels"][0]["status"] == "caught_up"


def test_page_transaction_rolls_back_messages_and_cursor(store):
    store.discover(channel())
    old = store.cursor("10")
    with pytest.raises(ValueError):
        store.save_page("10", [message(int(old) + 1), message(int(old) + 2, "other")])
    assert store.cursor("10") == old
    assert store.status()["messages"] == 0


def test_cross_guild_guard(store):
    with pytest.raises(ValueError):
        store.discover(channel(guild="2"))
    with pytest.raises(ValueError):
        mod.Store(store.directory, "2")
    assert store.status()["channels"] == []


def test_access_failures_not_marked_complete(store):
    api = FakeAPI(store)
    api.fail.add("/channels/10/messages")
    result = mod.Collector(api, store).tick()
    assert result["channels"][0]["status"] == "http_403"
    assert store.cursor("10") == str(store.initial_after())
    assert result["issues"]


def test_guild_failure_preserves_state_and_reports_issue(store):
    api = FakeAPI(store)
    api.fail.add("/guilds/1/channels")
    result = mod.Collector(api, store).tick()
    assert result["issues"] == [{"scope": "guild_channels", "status": 403}]
    assert json.loads((store.directory / "coverage.json").read_text())["issues"]


def test_edit_upsert_and_source_url(store):
    api = FakeAPI(store)
    mod.Collector(api, store).tick()
    api.messages["10"][-1]["content"] = "바뀐 내용"
    api.messages["10"][-1]["edited_timestamp"] = "2026-11-17T00:10:00Z"
    mod.Collector(api, store).tick()
    row = next(r for r in store.recent() if r["id"] == api.messages["10"][-1]["id"])
    assert row["content"] == "바뀐 내용"
    assert row["edited"]
    assert row["url"] == f"https://discord.com/channels/1/10/{row['id']}"
    assert store.status()["messages"] == 3


def test_threads_and_unsupported_types(store):
    api = FakeAPI(store)
    api.channels += [channel("20", 15), channel("30", 4)]
    api.active = [channel("40", 11)]
    api.archive["/channels/20/threads/archived/public"] = {
        "threads": [channel("50", 11)], "has_more": False}
    mod.Collector(api, store).tick()
    paths = [p for p, _ in api.calls]
    assert "/channels/40/messages" in paths
    assert "/channels/50/messages" in paths
    assert "/channels/20/messages" not in paths
    assert "/channels/30/messages" not in paths
    assert not any("/guilds/2/" in p or "/users/@me/channels" in p for p in paths)


def test_archive_cap_is_visible(store):
    api = FakeAPI(store)
    thread = channel("50", 11)
    thread["thread_metadata"] = {"archive_timestamp": "2026-11-01T00:00:00Z"}
    api.archive["/channels/10/threads/archived/public"] = {"threads": [thread], "has_more": True}
    result = mod.Collector(api, store, max_pages=1).tick()
    assert any(i["status"] == "archive_discovery_capped" for i in result["issues"])


def test_old_messages_excluded_by_horizon(store):
    api = FakeAPI(store, 0)
    api.messages["10"] = [message(store.initial_after() - 1)]
    mod.Collector(api, store).tick()
    assert store.status()["messages"] == 0


def test_permissions_and_horizon_persist(store):
    assert store.directory.stat().st_mode & 0o777 == 0o700
    assert (store.directory / "discord.sqlite3").stat().st_mode & 0o777 == 0o600
    seed = store.initial_after()
    reopened = mod.Store(store.directory, "1", days=90)
    assert reopened.initial_after() == seed
    reopened.db.close()


def test_retry_429_then_success():
    calls, delays = [], []
    def opener(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 429, "limited", Message(), io.BytesIO(b'{"retry_after":0.1}'))
        return io.BytesIO(b'[]')
    assert mod.Discord("secret", opener, delays.append).get("/guilds/1/channels") == []
    assert len(calls) == 2
    assert all(r.get_method() == "GET" for r in calls)
    assert delays


def test_http_failure_never_echoes_secret():
    def opener(request, timeout):
        raise HTTPError(request.full_url, 401, "secret credential", Message(), io.BytesIO(b'secret'))
    with pytest.raises(mod.APIError) as exc:
        mod.Discord("secret", opener, lambda _: None).get("/guilds/1/channels")
    assert str(exc.value) == "discord_http_401"


def test_transport_retry_is_bounded():
    calls = []
    def opener(request, timeout):
        calls.append(1)
        raise URLError("secret")
    with pytest.raises(mod.APIError):
        mod.Discord("secret", opener, lambda _: None).get("/guilds/1/channels")
    assert len(calls) == 4
