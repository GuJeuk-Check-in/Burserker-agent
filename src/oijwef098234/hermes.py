"""Hermes Agent 연결 — `hermes acp`(Agent Client Protocol, 줄 단위 JSON-RPC)를 자식 프로세스로 띄워 대화.

- 대화(세션)는 Hermes가 기억 → 앱은 매번 새 메시지만 보냄
- 답은 조각(agent_message_chunk)으로 흘러옴 → on_event("chunk", 글)
- Hermes가 위험한 명령을 실행하려 하면 session/request_permission → on_event("permission", …)로 화면에 물어봄
- 모든 함수는 워커 스레드에서 부름 (화면 갱신은 부르는 쪽이 call_from_thread로)
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

EventFn = Callable[[str, dict], object]


class HermesError(Exception):
    pass


class HermesAuthError(HermesError):
    """모델 제공자 로그인이 필요함."""


AUTH_HINTS = ("provider", "token", "auth", "sign", "login", "credential", "api key")


def hermes_bin() -> str | None:
    if found := shutil.which("hermes"):
        return found
    local = Path.home() / ".local/bin/hermes"
    return str(local) if local.exists() else None


def workdir() -> Path:
    """Hermes가 일하는 빈 폴더 (프로젝트 AGENTS.md 등을 읽지 않게)."""
    base = os.environ.get("OIJWEF098234_DATA_DIR") or (Path(os.environ.get("XDG_DATA_HOME", "~/.local/share"))
                                                        .expanduser() / "oijwef098234")
    d = Path(base).expanduser() / "hermes"
    d.mkdir(parents=True, exist_ok=True)
    return d


class HermesEngine:
    """`hermes acp` 한 프로세스 + 한 세션. ask()는 한 번에 하나만 (앱에서 ai_busy로 막음)."""

    name = "Hermes"

    def __init__(self, cmd: list[str] | None = None, *, cwd: Path | None = None,
                 session_cwd: str | None = None, client_name: str = "oijwef098234") -> None:
        self.cmd = cmd
        self.cwd = cwd
        self.session_cwd = session_cwd
        self.client_name = client_name
        self.proc: subprocess.Popen | None = None
        self.session_id: str | None = None
        self._ids = itertools.count(1)
        self._wait: dict[int, tuple[threading.Event, dict]] = {}
        self._lock = threading.Lock()
        self._on_event: EventFn | None = None

    # ── 프로세스 ────────────────────────────────────────────
    def _start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        cmd = self.cmd
        if cmd is None:
            exe = hermes_bin()
            if not exe:
                raise HermesError("hermes 명령을 찾을 수 없어요 (Hermes Agent 설치 필요)")
            cmd = [exe, "acp"]
        directory = self.cwd if self.cwd is not None else workdir()
        directory.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, cwd=directory, bufsize=1)
        self.session_id = None
        threading.Thread(target=self._read, daemon=True).start()
        res = self._call("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": self.client_name, "version": "0.1.0"},
        }, timeout=60)
        if not isinstance(res, dict):
            raise HermesError("Hermes가 응답하지 않아요")

    def close(self) -> None:
        p, self.proc = self.proc, None
        self.session_id = None
        if p and p.poll() is None:
            p.kill()

    def _write(self, msg: dict) -> None:
        p = self.proc
        if not p or p.poll() is not None or p.stdin is None:
            raise HermesError("Hermes가 꺼졌어요")
        with self._lock:
            p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            p.stdin.flush()

    def _call(self, method: str, params: dict, timeout: float = 600) -> object:
        rid = next(self._ids)
        ev, box = threading.Event(), {}
        self._wait[rid] = (ev, box)
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if not ev.wait(timeout):
            self._wait.pop(rid, None)
            raise HermesError("Hermes 응답이 너무 오래 걸려요")
        if "error" in box:
            err = box["error"] or {}
            detail = str((err.get("data") or {}).get("details") or err.get("message") or "Hermes 오류")
            if any(h in detail.lower() for h in AUTH_HINTS):
                raise HermesAuthError(detail)
            raise HermesError(detail[:300])
        return box.get("result")

    def _read(self) -> None:
        p = self.proc
        assert p is not None and p.stdout is not None
        for line in p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
                ev, box = self._wait.pop(msg["id"], (None, None))
                if ev is not None:
                    box.update(msg)
                    ev.set()
            elif msg.get("method") == "session/update":
                upd = (msg.get("params") or {}).get("update") or {}
                self._emit_update(upd)
            elif msg.get("method") == "session/request_permission" and "id" in msg:
                threading.Thread(target=self._permission, args=(msg,), daemon=True).start()
            elif "id" in msg and "method" in msg:          # 모르는 요청은 거절 (파일·터미널 권한 안 줌)
                self._write({"jsonrpc": "2.0", "id": msg["id"],
                             "error": {"code": -32601, "message": "not supported"}})
        for ev, box in list(self._wait.values()):           # 프로세스가 끝나면 기다리던 것들 깨움
            box["error"] = {"message": "Hermes가 꺼졌어요"}
            ev.set()
        self._wait.clear()

    def _emit(self, kind: str, data: dict) -> object:
        return self._on_event(kind, data) if self._on_event else None

    def _emit_update(self, upd: dict) -> None:
        kind = upd.get("sessionUpdate")
        if kind == "agent_message_chunk":
            c = upd.get("content") or {}
            if c.get("type") == "text":
                self._emit("chunk", {"text": c.get("text", "")})
        elif kind in ("tool_call", "tool_call_update"):
            self._emit("tool", {"id": upd.get("toolCallId"), "title": upd.get("title") or "",
                                "status": upd.get("status") or ""})

    def _permission(self, msg: dict) -> None:
        params = msg.get("params") or {}
        options = params.get("options") or []
        try:
            choice = self._emit("permission", {"title": (params.get("toolCall") or {}).get("title") or "",
                                               "raw": (params.get("toolCall") or {}).get("rawInput") or {},
                                               "options": options})
        except Exception:  # noqa: BLE001 - 물어보다 실패하면 거절
            choice = None
        ids = {o.get("optionId") for o in options}
        outcome = ({"outcome": "selected", "optionId": choice} if choice in ids
                   else {"outcome": "cancelled"})
        try:
            self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": outcome}})
        except HermesError:
            pass

    # ── 대화 ────────────────────────────────────────────────
    def new_conversation(self) -> None:
        self.session_id = None

    def ask(self, text: str, on_event: EventFn | None = None) -> str:
        """메시지 하나 보내고 답이 끝날 때까지 기다림 (워커 스레드). 답 전체 글을 돌려줌."""
        parts: list[str] = []
        user_cb = on_event

        def collect(kind: str, data: dict) -> object:
            if kind == "chunk":
                parts.append(data["text"])
            return user_cb(kind, data) if user_cb else None
        self._on_event = collect
        try:
            self._start()
            if self.session_id is None:
                directory = self.session_cwd or str(self.cwd if self.cwd is not None else workdir())
                res = self._call("session/new", {"cwd": directory, "mcpServers": []}, timeout=120)
                self.session_id = (res or {}).get("sessionId")
                if not self.session_id:
                    raise HermesError("Hermes 대화를 시작하지 못했어요")
            self._call("session/prompt", {"sessionId": self.session_id,
                                          "prompt": [{"type": "text", "text": text}]}, timeout=900)
        finally:
            self._on_event = None
        return "".join(parts)

    def cancel(self) -> None:
        if self.session_id and self.proc and self.proc.poll() is None:
            try:
                self._write({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.session_id}})
            except HermesError:
                pass
