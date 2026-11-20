"""Read-only, single-guild Discord collector. No app DB or external writes.

Polling records new messages and reconciles the newest 100 for edits. It does
NOT establish deletion history, recover inaccessible private threads, download
attachments, transcribe voice, or send raw content to a model.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MESSAGE_TYPES = {0, 2, 5, 10, 11, 12, 13}
THREAD_PARENTS = {0, 5, 15, 16}
DISCORD_EPOCH_MS = 1420070400000


def snowflake_since(days: int) -> str:
    return str(max(0, int(time.time() * 1000) - days * 86400000 - DISCORD_EPOCH_MS) << 22)


class APIError(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"discord_http_{status}")


class Discord:
    def __init__(self, token: str, opener=urlopen, sleep=time.sleep):
        self.token, self.opener, self.sleep = token, opener, sleep

    def get(self, path: str, **params):
        if not path.startswith(("/guilds/", "/channels/")) or ".." in path:
            raise ValueError("unsupported route")
        url = "https://discord.com/api/v10" + path
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={"Authorization": "Bot " + self.token,
                                       "User-Agent": "GujeukPMCollector/0.1"}, method="GET")
        for attempt in range(4):
            try:
                with self.opener(request, timeout=30) as response:
                    data = json.load(response)
                self.sleep(0.15)
                return data
            except HTTPError as error:
                if error.code == 429:
                    try:
                        delay = float(json.loads(error.read()).get("retry_after", 2))
                    except (ValueError, TypeError):
                        delay = 2
                    if delay > 60 or attempt == 3:
                        raise APIError(429) from None
                    self.sleep(max(0.2, delay))
                elif error.code >= 500 and attempt < 3:
                    self.sleep(2 ** attempt)
                else:
                    raise APIError(error.code) from None
            except (URLError, TimeoutError, OSError):
                if attempt == 3:
                    raise APIError(0) from None
                self.sleep(2 ** attempt)
        raise APIError(0)


class Store:
    def __init__(self, directory: Path, guild: str, days: int = 30, *, read_only=False):
        if read_only:
            self.directory, self.guild = directory, guild
            self.db = sqlite3.connect(f'{(directory / "discord.sqlite3").resolve().as_uri()}?mode=ro', uri=True)
            row = self.db.execute("SELECT value FROM settings WHERE key='guild'").fetchone()
            if row is None or row[0] != guild:
                self.db.close()
                raise ValueError('database belongs to another guild')
            return
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink():
            raise ValueError("state directory must not be a symlink")
        os.chmod(directory, 0o700)
        self.directory, self.guild = directory, guild
        path = directory / "discord.sqlite3"
        if path.is_symlink():
            raise ValueError("database must not be a symlink")
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS channels(
                id TEXT PRIMARY KEY, name TEXT, type INTEGER, parent TEXT,
                cursor TEXT NOT NULL, status TEXT NOT NULL, checked REAL);
            CREATE TABLE IF NOT EXISTS messages(
                id TEXT PRIMARY KEY, channel TEXT NOT NULL, author TEXT,
                created TEXT, edited TEXT, content TEXT, attachments INTEGER,
                url TEXT NOT NULL, observed REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS message_polls(message_id TEXT PRIMARY KEY, data TEXT NOT NULL);
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO settings VALUES('guild',?)", (guild,))
            self.db.execute("INSERT OR IGNORE INTO settings VALUES('initial_after',?)", (snowflake_since(days),))
        if self.db.execute("SELECT value FROM settings WHERE key='guild'").fetchone()[0] != guild:
            self.db.close()
            raise ValueError("database belongs to another guild")

    def discover(self, channel: dict):
        if str(channel.get("guild_id", self.guild)) != self.guild:
            raise ValueError("cross-guild channel rejected")
        seed = self.db.execute("SELECT value FROM settings WHERE key='initial_after'").fetchone()[0]
        with self.db:
            self.db.execute("""INSERT INTO channels VALUES(?,?,?,?,?,'discovered',NULL)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,type=excluded.type,parent=excluded.parent""",
                (str(channel["id"]), channel.get("name", ""), channel["type"], channel.get("parent_id"), seed))

    def channel_ids(self):
        return [r[0] for r in self.db.execute("SELECT id FROM channels WHERE type IN (0,2,5,10,11,12,13)")]

    def cursor(self, channel: str) -> str:
        row = self.db.execute("SELECT cursor FROM channels WHERE id=?", (channel,)).fetchone()
        if row is None:
            raise ValueError("unknown channel")
        return row[0]

    def initial_after(self) -> int:
        return int(self.db.execute("SELECT value FROM settings WHERE key='initial_after'").fetchone()[0])

    def save_page(self, channel: str, messages: list[dict], advance=True):
        old = self.cursor(channel)
        now = time.time()
        with self.db:
            for m in messages:
                if str(m.get("channel_id", channel)) != channel:
                    raise ValueError("cross-channel message rejected")
                if str(m.get("guild_id", self.guild)) != self.guild:
                    raise ValueError("cross-guild message rejected")
                ident = str(m["id"])
                int(ident)
                self.db.execute("""INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET edited=excluded.edited,content=excluded.content,
                    attachments=excluded.attachments,observed=excluded.observed""",
                    (ident, channel, str(m.get("author", {}).get("id", "")), m.get("timestamp"),
                     m.get("edited_timestamp"), m.get("content", ""), len(m.get("attachments", [])),
                     f"https://discord.com/channels/{self.guild}/{channel}/{ident}", now))
                poll = m.get('poll')
                if poll is not None:
                    results = poll.get('results') or {}
                    counts = {r['id']: r.get('count') for r in results.get('answer_counts', [])}
                    value = {'question': poll.get('question', {}).get('text', '')[:1000],
                        'answers': [{'id': a['answer_id'],
                            'text': a.get('poll_media', {}).get('text', '')[:1000],
                            'emoji': ((a.get('poll_media', {}).get('emoji') or {}).get('name') or '')[:100],
                            'count': counts.get(a['answer_id'])} for a in poll.get('answers', [])[:20]],
                        'allow_multiselect': poll.get('allow_multiselect'), 'expiry': poll.get('expiry'),
                        'is_finalized': results.get('is_finalized')}
                    self.db.execute('INSERT OR REPLACE INTO message_polls VALUES(?,?)',
                                    (ident, json.dumps(value, ensure_ascii=False)))
                else:
                    self.db.execute('DELETE FROM message_polls WHERE message_id=?', (ident,))
            if advance and messages:
                cursor = str(max([int(old)] + [int(m["id"]) for m in messages]))
                self.db.execute("UPDATE channels SET cursor=? WHERE id=?", (cursor, channel))

    def mark(self, channel: str, status: str):
        with self.db:
            self.db.execute("UPDATE channels SET status=?,checked=? WHERE id=?", (status, time.time(), channel))

    def status(self):
        return {"guild": self.guild,
                "messages": self.db.execute("SELECT count(*) FROM messages").fetchone()[0],
                "empty_content_messages": self.db.execute("SELECT count(*) FROM messages WHERE content='' ").fetchone()[0],
                "channels": [{"id": r[0], "type": r[1], "status": r[2], "checked": r[3]}
                             for r in self.db.execute("SELECT id,type,status,checked FROM channels ORDER BY id")]}

    def recent(self, limit=50):
        self.db.row_factory = sqlite3.Row
        try:
            return self._decode_messages(self.db.execute(self._message_select() +
                " ORDER BY m.observed DESC,CAST(m.id AS INTEGER) DESC LIMIT ?", (limit,)))
        finally:
            self.db.row_factory = None

    def _message_select(self):
        # Old read-only snapshots remain readable before the writer migrates.
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_polls'").fetchone()
        select = ('SELECT m.*,c.name AS channel_name,pc.name AS parent_name,'
                  + ('p.data' if exists else 'NULL') + ' AS poll FROM messages m '
                  'LEFT JOIN channels c ON c.id=m.channel LEFT JOIN channels pc ON pc.id=c.parent')
        return select + (' LEFT JOIN message_polls p ON p.message_id=m.id' if exists else '')

    @staticmethod
    def _decode_messages(rows):
        result = []
        for row in rows:
            value = dict(row)
            value['poll'] = json.loads(value['poll']) if value['poll'] is not None else None
            result.append(value)
        return result

    def backup_to(self, path):
        if path.is_symlink() or path.exists():
            raise ValueError('backup destination must be new')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        target = sqlite3.connect(path)
        try:
            self.db.backup(target)
            if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('backup verification failed')
        finally:
            target.close()

    def changed(self, cursor=None, limit=1000):
        observed, ident = cursor or [0, '0']
        self.db.row_factory = sqlite3.Row
        try:
            return self._decode_messages(self.db.execute(self._message_select() + '''
                WHERE m.observed>? OR (m.observed=? AND CAST(m.id AS INTEGER)>?)
                ORDER BY m.observed,CAST(m.id AS INTEGER) LIMIT ?''', (observed, observed, int(ident), limit)))
        finally:
            self.db.row_factory = None

    def archive_cursor(self, route):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', ('archive:' + route,)).fetchone()
        return row[0] if row else None

    def set_archive_cursor(self, route, value):
        with self.db:
            if value is None:
                self.db.execute('DELETE FROM settings WHERE key=?', ('archive:' + route,))
            else:
                self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('archive:' + route, value))

    def write_coverage(self, coverage):
        target = self.directory / "coverage.json"
        temporary = self.directory / "coverage.tmp"
        with temporary.open("w") as f:
            os.chmod(temporary, 0o600)
            json.dump(coverage, f, ensure_ascii=False, indent=2)
        temporary.replace(target)


class Collector:
    def __init__(self, api, store: Store, max_pages=5):
        self.api, self.store, self.max_pages = api, store, max_pages

    def tick(self):
        guild = self.store.guild
        coverage = {"guild": guild, "checked": time.time(), "issues": [],
                    "limits": ["initial history: configured horizon (default 30 days)",
                               "polling: deletions and edits outside newest 100 not tracked",
                               "no audio transcription or attachment download",
                               "private threads: joined/accessible only; old archive discovery is bounded",
                               "deleted channels retained as historical data, not treated as current access"]}
        try:
            channels = self.api.get(f"/guilds/{guild}/channels")
        except APIError as error:
            coverage["issues"].append({"scope": "guild_channels", "status": error.status})
            coverage.update(self.store.status())
            self.store.write_coverage(coverage)
            return coverage
        for c in channels:
            self.store.discover(c)
            if c["type"] not in MESSAGE_TYPES:
                self.store.mark(str(c["id"]), "container" if c["type"] in {4, 15, 16} else "unsupported")
        try:
            active = self.api.get(f"/guilds/{guild}/threads/active")
            for thread in active.get("threads", []):
                self.store.discover(thread)
        except APIError as error:
            coverage["issues"].append({"scope": "active_threads", "status": error.status})
        for parent in channels:
            if parent["type"] not in THREAD_PARENTS:
                continue
            for kind in (("public", "joined_private") if parent["type"] == 0 else ("public",)):
                route = (f"/channels/{parent['id']}/threads/archived/public" if kind == "public"
                         else f"/channels/{parent['id']}/users/@me/threads/archived/private")
                before = self.store.archive_cursor(route)
                for page in range(self.max_pages):
                    params: dict[str, int | str] = {"limit": 100}
                    if before:
                        params["before"] = before
                    try:
                        data = self.api.get(route, **params)
                    except APIError as error:
                        coverage["issues"].append({"scope": f"archive:{parent['id']}:{kind}", "status": error.status})
                        break
                    threads = data.get("threads", [])
                    for thread in threads:
                        self.store.discover(thread)
                    if not data.get("has_more"):
                        self.store.set_archive_cursor(route, None)
                        break
                    if not threads:
                        coverage["issues"].append({"scope": route, "status": "empty_archive_page"})
                        break
                    new_before = (str(threads[-1]["id"]) if kind == "joined_private"
                                  else threads[-1]["thread_metadata"]["archive_timestamp"])
                    if new_before == before:
                        coverage["issues"].append({"scope": route, "status": "archive_no_progress"})
                        break
                    before = new_before
                    self.store.set_archive_cursor(route, before)
                    if page == self.max_pages - 1:
                        coverage["issues"].append({"scope": route, "status": "archive_discovery_capped"})
        # Previously discovered archived threads may no longer appear in the bounded list.
        # Retry them too, retaining explicit 403/404 instead of declaring deletion.
        targets = set(self.store.channel_ids())
        for ident in sorted(targets, key=int):
            try:
                caught_up = False
                for _ in range(self.max_pages):
                    cursor = self.store.cursor(ident)
                    batch = self.api.get(f"/channels/{ident}/messages", after=cursor, limit=100)
                    batch = sorted(batch, key=lambda m: int(m["id"]))
                    if batch and any(int(m["id"]) <= int(cursor) for m in batch):
                        raise ValueError("non-advancing API page")
                    self.store.save_page(ident, batch)
                    if len(batch) < 100:
                        caught_up = True
                        break
                recent = self.api.get(f"/channels/{ident}/messages", limit=100)
                seed = self.store.initial_after()
                self.store.save_page(ident, [m for m in recent if int(m["id"]) > seed], advance=False)
                self.store.mark(ident, "caught_up" if caught_up else "catching_up")
            except APIError as error:
                self.store.mark(ident, f"http_{error.status}")
                coverage["issues"].append({"scope": f"messages:{ident}", "status": error.status})
        coverage.update(self.store.status())
        coverage["finished"] = time.time()
        self.store.write_coverage(coverage)
        return coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guild", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval", type=int, default=120)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--recent", type=int, default=0, help="emit source content for owner-only analysis, never logs")
    args = parser.parse_args()
    if args.days < 1 or args.interval < 30 or not args.guild.isdigit() or not 0 <= args.recent <= 200:
        parser.error("invalid bounds")
    os.umask(0o077)
    store = Store(args.state, args.guild, args.days)
    if args.status or args.recent:
        print(json.dumps(store.recent(args.recent) if args.recent else store.status(), ensure_ascii=False))
        return
    token = args.token_file.read_text().strip() if args.token_file else os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        parser.error("Discord credential missing")
    collector = Collector(Discord(token), store)
    while True:
        try:
            if shutil.disk_usage(args.state).free < 1024 ** 3:
                raise RuntimeError("low_disk")
            if sum(p.stat().st_size for p in args.state.glob('discord.sqlite3*')) > 512 * 1024 ** 2:
                raise RuntimeError("collection_size_limit")
            result = collector.tick()
            print(json.dumps({"messages": result["messages"], "channels": len(result["channels"]),
                              "issues": len(result["issues"]), "checked": result["checked"]}), flush=True)
        except Exception as error:
            # Never print request headers, message content or arbitrary exception values.
            print(json.dumps({"collector_error": type(error).__name__}), flush=True)
            if args.once:
                raise SystemExit(1) from None
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
