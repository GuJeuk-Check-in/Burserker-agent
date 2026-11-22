"""구즉 전용 로컬 저장소. 개인 일정·EventKit·운영 서버와 연결하지 않아요.

Notion 연결 전의 항목은 모두 로컬 초안이에요. 연동된 공식 태스크로
승격하거나 Notion 동기화 완료라고 표시하지 않아요.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

STATES = {"doing": "진행중", "blocked": "보류", "done": "완료"}
MAJORS = ("플러터", "백엔드", "프론트엔드", "디자인")
KINDS = {"task": "태스크", "event": "일정"}
FIELDS = {"kind", "title", "subtitle", "major", "owner", "assignees", "begins", "due", "start", "status", "note"}


def default_db_path() -> Path:
    base = os.environ.get("GUJEUK_AI_DATA_DIR")
    root = Path(base).expanduser() if base else Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "gujeuk-ai"
    return root / "gujeuk-ai.db"


def validate(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) - FIELDS:
        raise ValueError("알 수 없는 입력 항목이 있어요")
    result = {"kind": "task", "title": "", "subtitle": "", "major": "", "owner": "", "assignees": [],
              "begins": "", "due": "", "start": "", "status": "blocked", "note": ""} | data
    if any(not isinstance(v, str) for k, v in result.items() if k != "assignees"):
        raise ValueError("입력 값은 글자로 전달해 주세요")
    result = {k: v.strip() if isinstance(v, str) else v for k, v in result.items()}
    names = result["assignees"]
    if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
        raise ValueError("담당자는 이름 목록으로 전달해 주세요")
    if "assignees" not in data and result["owner"]:
        names = [result["owner"]]  # A legacy single name must not be split heuristically.
    result["assignees"] = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    result["owner"] = ", ".join(result["assignees"])
    if result["kind"] == "todo":
        result["kind"] = "task"
    if result["status"] == "todo":
        result["status"] = "blocked"
    if result["major"] and result["major"] not in MAJORS:
        raise ValueError("전공을 확인해 주세요")
    if not result["title"] or len(result["title"]) > 300:
        raise ValueError("제목을 1~300자로 입력해 주세요")
    if result["kind"] not in KINDS or result["status"] not in STATES:
        raise ValueError("종류 또는 진행 상태를 확인해 주세요")
    if result["due"]:
        try:
            if date.fromisoformat(result["due"]).isoformat() != result["due"]:
                raise ValueError
        except ValueError:
            raise ValueError("날짜는 YYYY-MM-DD로 입력해 주세요") from None
    if result["begins"]:
        try:
            if date.fromisoformat(result["begins"]).isoformat() != result["begins"]:
                raise ValueError
        except ValueError:
            raise ValueError("시작 날짜는 YYYY-MM-DD로 입력해 주세요") from None
        if not result["due"] or result["begins"] > result["due"]:
            raise ValueError("기간의 종료일은 시작일 이후여야 해요")
    if result["kind"] == "event" and not result["due"]:
        raise ValueError("일정에는 날짜가 필요해요")
    if result["start"]:
        try:
            if datetime.strptime(result["start"], "%H:%M").strftime("%H:%M") != result["start"]:
                raise ValueError
        except ValueError:
            raise ValueError("시간은 HH:MM으로 입력해 주세요") from None
        if not result["due"]:
            raise ValueError("시각을 입력하려면 날짜가 필요해요")
    return result


@dataclass(frozen=True)
class Item:
    id: int
    kind: str
    title: str
    owner: str
    due: str
    start: str
    status: str
    note: str
    source: str
    source_key: str
    url: str
    version: int
    subtitle: str
    major: str
    assignees: list[str]
    begins: str

    def editable(self):
        return {k: v for k, v in asdict(self).items() if k in FIELDS}


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_db_path()
        # Separate schema as well as filename; accidentally pointing at the personal DB
        # must fail BEFORE any schema or PRAGMA mutation.
        if self.path.exists():
            probe = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                names = {r[0] for r in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if names and "gujeuk_meta" not in names:
                    raise ValueError("구즉 전용 DB가 아니에요. 개인 DB와 다른 경로를 사용해 주세요")
            finally:
                probe.close()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("DB 심볼릭 링크는 사용할 수 없어요")
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS gujeuk_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT OR IGNORE INTO gujeuk_meta VALUES('authority','notion');
            CREATE TABLE IF NOT EXISTS items(
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                owner TEXT NOT NULL, due TEXT NOT NULL, start TEXT NOT NULL,
                status TEXT NOT NULL, note TEXT NOT NULL,
                source TEXT NOT NULL, source_key TEXT NOT NULL, url TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1);
            CREATE UNIQUE INDEX IF NOT EXISTS item_source ON items(source,source_key) WHERE source_key!='';
            CREATE TABLE IF NOT EXISTS proposals(
                id INTEGER PRIMARY KEY, source_key TEXT UNIQUE NOT NULL, item_id INTEGER NOT NULL,
                base_version INTEGER NOT NULL, data TEXT NOT NULL, url TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending');
            CREATE TABLE IF NOT EXISTS audit(
                id INTEGER PRIMARY KEY, item_id INTEGER, action TEXT NOT NULL, data TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        """)
        # Additive migration: keep IDs, source keys, audit and pending approvals.
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(items)")}
        with self.db:
            proposal_columns = {r[1] for r in self.db.execute('PRAGMA table_info(proposals)')}
            if 'created_at' not in proposal_columns:
                self.db.execute("ALTER TABLE proposals ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
            self.db.execute('''CREATE TABLE IF NOT EXISTS proposal_observations(
                source_key TEXT PRIMARY KEY, proposal_id INTEGER NOT NULL, data TEXT NOT NULL,
                url TEXT NOT NULL, created_at TEXT NOT NULL)''')
            for name, declaration in {"subtitle": "TEXT NOT NULL DEFAULT ''", "major": "TEXT NOT NULL DEFAULT ''",
                                      "assignees": "TEXT NOT NULL DEFAULT '[]'", "begins": "TEXT NOT NULL DEFAULT ''"}.items():
                if name not in columns:
                    self.db.execute(f"ALTER TABLE items ADD COLUMN {name} {declaration}")
            if "assignees" not in columns:
                for row in self.db.execute("SELECT id,owner FROM items WHERE owner!=''").fetchall():
                    self.db.execute("UPDATE items SET assignees=? WHERE id=?", (json.dumps([row['owner']], ensure_ascii=False), row['id']))
            self.db.execute("UPDATE items SET kind='task' WHERE kind='todo'")
            self.db.execute("UPDATE items SET status='blocked' WHERE status='todo'")
            self.db.execute("INSERT OR REPLACE INTO gujeuk_meta VALUES('schema','2')")
            self.db.execute("""CREATE TABLE IF NOT EXISTS estimates(
                item_id INTEGER PRIMARY KEY, version INTEGER NOT NULL, percent INTEGER NOT NULL,
                reason TEXT NOT NULL, evidence TEXT NOT NULL, revision TEXT NOT NULL, analyzed_at TEXT NOT NULL)""")
        os.chmod(self.path, 0o600)

    def close(self):
        self.db.close()

    def items(self, kind: str = "", owner: str = "") -> list[Item]:
        rows = self.db.execute("""SELECT * FROM items WHERE (?='' OR kind=?) AND (?='' OR owner=?)
            ORDER BY CASE WHEN due='' THEN 1 ELSE 0 END,due,start,id""", (kind, kind, "", ""))
        items = [self._item(r) for r in rows]
        return [i for i in items if not owner or owner in i.assignees]

    @staticmethod
    def _item(row):
        data = dict(row)
        data["assignees"] = json.loads(data["assignees"])
        return Item(**data)

    @staticmethod
    def _params(data):
        return data | {"assignees": json.dumps(data["assignees"], ensure_ascii=False)}

    def get(self, ident: int) -> Item:
        row = self.db.execute("SELECT * FROM items WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise ValueError("항목을 찾을 수 없어요")
        return self._item(row)

    def add(self, data: dict, *, source="local", source_key="", url="") -> int:
        clean = validate(data)
        if source not in {"local", "discord"}:
            raise ValueError("Notion 가져오기는 아직 연결되지 않았어요")
        with self.db:
            if source_key:
                row = self.db.execute("SELECT id FROM items WHERE source=? AND source_key=?", (source, source_key)).fetchone()
                if row:
                    return row[0]
            cursor = self.db.execute("""INSERT INTO items(kind,title,owner,due,start,status,note,subtitle,major,assignees,begins,source,source_key,url)
                VALUES(:kind,:title,:owner,:due,:start,:status,:note,:subtitle,:major,:assignees,:begins,:source,:source_key,:url)""",
                self._params(clean) | {"source": source, "source_key": source_key, "url": url})
            ident = cursor.lastrowid
            assert ident is not None
            self.db.execute("INSERT INTO audit(item_id,action,data) VALUES(?,?,?)", (ident, "create", json.dumps(clean, ensure_ascii=False)))
        return ident

    def update(self, ident: int, data: dict, expected: int):
        previous = self.get(ident)
        if ("owner" in data and data["owner"] != previous.owner
                and data.get("assignees") == previous.assignees):
            data = data | {"assignees": [data["owner"]] if data["owner"] else []}
        clean = validate(data)
        with self.db:
            changed = self.db.execute("""UPDATE items SET kind=:kind,title=:title,owner=:owner,due=:due,
                start=:start,status=:status,note=:note,subtitle=:subtitle,major=:major,assignees=:assignees,begins=:begins,version=version+1
                WHERE id=:id AND version=:version""", self._params(clean) | {"id": ident, "version": expected})
            if changed.rowcount != 1:
                raise ValueError("다른 변경이 먼저 반영됐어요. 새로고침 후 다시 확인해 주세요")
            self.db.execute("INSERT INTO audit(item_id,action,data) VALUES(?,?,?)", (ident, "update", json.dumps(clean, ensure_ascii=False)))

    def proposals(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM proposals WHERE state='pending' ORDER BY id")]

    @staticmethod
    def _proposal_delta(item, clean):
        return {k: v for k, v in clean.items() if v != item.editable()[k]}

    @staticmethod
    def _proposal_key(item, clean, url, *, automated=False):
        delta = Store._proposal_delta(item, clean)
        # Automated note wording must not re-enqueue an identical substantive change.
        # Explicit human note edits still use exact equality. Different source URLs
        # never collapse, preserving original-source permission boundaries.
        if automated:
            delta.pop('note', None)
        return item.id, item.version, url, json.dumps(delta, sort_keys=True, ensure_ascii=False)

    def propose_change(self, ident, version, patch, *, source_key, url='', automated=False):
        with self.db:
            if not self.db.in_transaction:
                self.db.execute('BEGIN IMMEDIATE')
            item = self.get(ident)
            if type(version) is not int or version != item.version:
                raise ValueError('원본 버전이 바뀌었어요. 새로고침 후 다시 제안해 주세요')
            if 'owner' in patch and 'assignees' not in patch:
                patch = patch | {'assignees': [patch['owner']] if patch['owner'] else []}
            clean = validate(item.editable() | patch)
            if not self._proposal_delta(item, clean):
                return {'proposal_id': None, 'skipped': 'unchanged'}
            timestamp = datetime.now(timezone.utc).isoformat()
            previous = self.db.execute('''SELECT o.proposal_id,o.data,o.url,p.item_id,p.base_version
                FROM proposal_observations o JOIN proposals p ON p.id=o.proposal_id
                WHERE o.source_key=?''', (source_key,)).fetchone()
            if previous:
                if (json.loads(previous['data']) != clean or previous['url'] != url
                        or previous['item_id'] != ident or previous['base_version'] != version):
                    raise ValueError('제안 요청 키를 다른 내용으로 재사용할 수 없어요')
                return {'proposal_id': previous[0], 'deduplicated': True}
            key = self._proposal_key(item, clean, url, automated=automated)
            for row in self.db.execute("SELECT * FROM proposals WHERE item_id=? AND base_version=? AND state='pending' ORDER BY id", (ident, version)):
                try:
                    candidate = validate(json.loads(row['data']))
                except (ValueError, TypeError):
                    continue
                if self._proposal_key(item, candidate, row['url'], automated=automated) == key:
                    self.db.execute('INSERT INTO proposal_observations VALUES(?,?,?,?,?)',
                        (source_key, row['id'], json.dumps(clean, ensure_ascii=False), url, timestamp))
                    return {'proposal_id': row['id'], 'deduplicated': True}
            cursor = self.db.execute('INSERT INTO proposals(source_key,item_id,base_version,data,url,created_at) VALUES(?,?,?,?,?,?)',
                (source_key, ident, version, json.dumps(clean, ensure_ascii=False), url, timestamp))
            self.db.execute('INSERT INTO proposal_observations VALUES(?,?,?,?,?)',
                (source_key, cursor.lastrowid, json.dumps(clean, ensure_ascii=False), url, timestamp))
            return {'proposal_id': cursor.lastrowid, 'deduplicated': False}

    def proposal_review(self):
        """Non-destructive projection; never expires, rejects or approves a record."""
        counts = dict.fromkeys(('pending_records', 'review_groups', 'duplicate_records',
            'note_variants', 'stale_version', 'no_change', 'orphaned', 'invalid',
            'age_unknown', 'older_than_14_days', 'observation_variants'), 0)
        groups, exact, substantive = [], {}, {}
        checked = datetime.now(timezone.utc).timestamp()
        for row in self.proposals():
            counts['pending_records'] += 1
            created = self.proposal_created_at(row)
            try:
                timestamp = datetime.fromisoformat(created)
                if timestamp.tzinfo is None:
                    raise ValueError('unknown timezone')
                if checked - timestamp.timestamp() > 14 * 86400:
                    counts['older_than_14_days'] += 1
            except (ValueError, TypeError):
                counts['age_unknown'] += 1
            try:
                clean = validate(json.loads(row['data']))
            except (ValueError, TypeError):
                counts['invalid'] += 1
                continue
            if row['item_id'] == 0:  # Legacy new-item proposals remain reviewable.
                key = core = (0, row['url'], json.dumps(clean, sort_keys=True, ensure_ascii=False))
            else:
                try:
                    item = self.get(row['item_id'])
                except ValueError:
                    counts['orphaned'] += 1
                    continue
                if row['base_version'] != item.version:
                    counts['stale_version'] += 1
                    continue
                if not self._proposal_delta(item, clean):
                    counts['no_change'] += 1
                    continue
                key = self._proposal_key(item, clean, row['url'])
                core = self._proposal_key(item, clean, row['url'], automated=True)
            if key in exact:
                counts['duplicate_records'] += 1
                exact[key]['review_member_ids'].append(row['id'])
            elif core in substantive:
                counts['note_variants'] += 1
                substantive[core]['review_member_ids'].append(row['id'])
                substantive[core]['review_flags'] = ['note_variants_require_review']
                exact[key] = substantive[core]
            else:
                group = row | {'review_member_ids': [row['id']], 'review_flags': []}
                groups.append(group)
                exact[key] = substantive[core] = group
            group = exact[key]
            variants = sum(json.loads(observation[0]) != clean for observation in self.db.execute(
                'SELECT data FROM proposal_observations WHERE proposal_id=?', (row['id'],)))
            counts['observation_variants'] += variants
            if variants:
                group['review_flags'] = ['note_variants_require_review']
        counts['review_groups'] = len(groups)
        return {'counts': counts, 'groups': groups, 'automatic_resolution': False,
                'meaning': '앱 초안 변경 제안이에요. 공식 미완료 업무 수가 아니며 원본 기록은 보존돼요.'}

    def proposal_created_at(self, row):
        return row.get('created_at', '')

    def receive_discord(self, payload: dict) -> tuple[str, int]:
        """Explicit structured bridge, NOT an LLM parser or a network sync.

        New task/event drafts auto-add; all existing-item updates require
        owner approval. No delete or arbitrary SQL/action accepted.
        """
        if not isinstance(payload, dict) or set(payload) - {"source_key", "url", "data", "item_id", "base_version"}:
            raise ValueError("Discord 입력 형식을 확인해 주세요")
        if not isinstance(payload.get("data"), dict):
            raise ValueError("변경 내용은 객체로 전달해 주세요")
        key, url = payload.get("source_key", ""), payload.get("url", "")
        parsed = urlparse(url) if isinstance(url, str) else None
        if (not isinstance(key, str) or not key.strip() or parsed is None
                or parsed.scheme != "https" or parsed.netloc != "discord.com"
                or not parsed.path.startswith("/channels/1413450780068937790/")):
            raise ValueError("구즉 Discord 원문 링크와 중복 방지 키가 필요해요")
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
            raise ValueError("Discord 메시지 원문 링크가 필요해요")
        duplicate = self.db.execute("SELECT id FROM items WHERE source='discord' AND source_key=?", (key,)).fetchone()
        if duplicate:
            return "created", duplicate[0]
        duplicate = self.db.execute("SELECT id FROM proposals WHERE source_key=?", (key,)).fetchone()
        if duplicate:
            return "proposal", duplicate[0]
        ident = payload.get("item_id", 0)
        if not isinstance(ident, int) or ident < 0:
            raise ValueError("항목 번호를 확인해 주세요")
        if ident:
            item = self.get(ident)
            version = payload.get("base_version")
            if not isinstance(version, int) or version != item.version:
                raise ValueError("원본 버전이 달라요. 최신 항목으로 다시 제안해 주세요")
            patch = payload["data"]
            if "owner" in patch and "assignees" not in patch:
                patch = patch | {"assignees": [patch["owner"]] if patch["owner"] else []}
            data = validate(item.editable() | patch)
        else:
            data, version = validate(payload.get("data", {})), 0
            return "created", self.add(data, source="discord", source_key=key, url=url)
        result = self.propose_change(ident, version, data, source_key=key, url=url)
        return ("unchanged", ident) if result.get('skipped') else ("proposal", result['proposal_id'])

    def resolve(self, ident: int, approve: bool):
        # One transaction: rejecting/approving twice cannot execute twice; stale
        # approvals remain pending for a new review rather than overwriting edits.
        with self.db:
            row = self.db.execute("SELECT * FROM proposals WHERE id=? AND state='pending'", (ident,)).fetchone()
            if row is None:
                raise ValueError("이미 처리됐거나 없는 제안이에요")
            if approve:
                data = json.loads(row["data"])
                if row["item_id"]:
                    clean = validate(data)
                    changed = self.db.execute("""UPDATE items SET kind=:kind,title=:title,owner=:owner,due=:due,
                        start=:start,status=:status,note=:note,subtitle=:subtitle,major=:major,assignees=:assignees,begins=:begins,
                        version=version+1 WHERE id=:id AND version=:version""",
                        self._params(clean) | {"id": row["item_id"], "version": row["base_version"]})
                    if changed.rowcount != 1:
                        raise ValueError("원본이 바뀌었어요. 이 제안은 재확인이 필요해요")
                    item_id = row["item_id"]
                else:
                    clean = validate(data)
                    result = self.db.execute("""INSERT INTO items(kind,title,owner,due,start,status,note,subtitle,major,assignees,begins,source,source_key,url)
                        VALUES(:kind,:title,:owner,:due,:start,:status,:note,:subtitle,:major,:assignees,:begins,'discord',:source_key,:url)""",
                        self._params(clean) | {"source_key": row["source_key"], "url": row["url"]})
                    item_id = result.lastrowid
                self.db.execute("INSERT INTO audit(item_id,action,data) VALUES(?,?,?)", (item_id, "approve", row["data"]))
            self.db.execute("UPDATE proposals SET state=? WHERE id=?", ("approved" if approve else "rejected", ident))

    def estimate(self, ident: int):
        row = self.db.execute("SELECT * FROM estimates WHERE item_id=?", (ident,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["evidence"] = json.loads(data["evidence"])
        data["stale"] = self.get(ident).version != data["version"]
        return data

    def record_estimate(self, ident: int, *, version: int, percent: int, reason: str,
                        evidence: list[str], revision: str, analyzed_at: str):
        """Persist an explicit AI report, never derive progress from task counts.

        This validates provenance structure, not the truth of model reasoning.
        It does not call GitHub/a model or mark the task completed.
        """
        if (type(percent) is not int or not 0 <= percent <= 100 or not isinstance(reason, str)
                or not reason.strip() or not isinstance(revision, str) or not revision.strip()):
            raise ValueError("AI 추정치와 판단 근거·기준 커밋이 필요해요")
        if self.get(ident).kind != "task" or self.get(ident).version != version:
            raise ValueError("최신 태스크에 대한 분석이 필요해요")
        if not isinstance(evidence, list) or not evidence or any(not isinstance(u, str) or urlparse(u).scheme != "https" or urlparse(u).netloc != "github.com" for u in evidence):
            raise ValueError("GitHub 코드·PR 근거 링크가 필요해요")
        try:
            timestamp = datetime.fromisoformat(analyzed_at)
            if timestamp.tzinfo is None:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("시간대가 있는 분석 시각이 필요해요") from None
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO estimates VALUES(?,?,?,?,?,?,?)", (
                ident, version, percent, reason, json.dumps(evidence), revision, analyzed_at))

    def calendar_items(self, first: date, last: date):
        return [i for i in self.items() if i.due and (i.begins or i.due) <= last.isoformat() and i.due >= first.isoformat()]

    def status(self):
        return {"authority": "notion", "notion_connected": False, "discord_sync_connected": False,
                "calendar_sync": False, "github_analysis_connected": False,
                "local_drafts": len(self.items()), "pending": len(self.proposals()), "database": str(self.path)}
