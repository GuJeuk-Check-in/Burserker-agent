"""Fail-closed Discord ingress patches for the pinned Hermes adapter.

The bot's own managed role counts as a mention. In participating bot threads,
human replies need a direct address, a bot reply target, or recent bot context.
User/role authorization and bot-to-bot raw mention rules remain upstream gates.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil

OLD = '''    def _self_is_explicitly_mentioned(self, message: Any) -> bool:
        """True when the bot is in ``message.mentions`` or raw-mentioned in the content."""
        if not self._client or not self._client.user:
            return False
        if self._client.user in getattr(message, "mentions", []):
            return True
        return str(self._client.user.id) in self._raw_mentioned_user_ids(message)
'''
NEW = '''    def _self_is_explicitly_mentioned(self, message: Any) -> bool:
        """Recognize this bot's user mention or its own managed Discord role."""
        if not self._client or not self._client.user:
            return False
        if self._client.user in getattr(message, "mentions", []):
            return True
        if str(self._client.user.id) in self._raw_mentioned_user_ids(message):
            return True
        # gujeuk-managed-bot-role-mention-v1: only Discord's managed role for THIS bot.
        for role in getattr(message, "role_mentions", []) or []:
            if (getattr(role, "managed", False)
                    and getattr(getattr(role, "tags", None), "bot_id", None) == self._client.user.id):
                return True
        return False
'''

ADMISSION_METHOD = '''    async def _burserker_admit_bot_thread_reply(self, message: Any) -> bool:
        """Admit an unmentioned human turn only when it continues a bot exchange."""
        if (not isinstance(message.channel, discord.Thread)
                or str(message.channel.id) not in self._threads
                or self._discord_thread_require_mention()
                or getattr(message.author, "bot", False)):
            return True

        content = getattr(message, "content", "") or ""
        if not self._client or not self._client.user:
            return False
        bot_id = str(self._client.user.id)
        # A reply ping can populate message.mentions without an inline token.
        # Only the raw token (or this bot's managed role token) is a direct address.
        if bot_id in self._raw_mentioned_user_ids(message):
            return True
        for role in getattr(message, "role_mentions", []) or []:
            if (getattr(role, "managed", False)
                    and str(getattr(getattr(role, "tags", None), "bot_id", "")) == bot_id
                    and f"<@&{role.id}>" in content):
                return True

        if self._raw_mentioned_user_ids(message) or re.search(r"<@&\\d+>", content):
            return False

        reference = getattr(message, "reference", None)
        resolved_mentions = getattr(message, "mentions", []) or []
        if any(str(getattr(user, "id", "")) != bot_id for user in resolved_mentions):
            return False
        if reference is None:
            if any(str(getattr(user, "id", "")) == bot_id for user in resolved_mentions):
                return True
        if reference is not None:
            target = getattr(reference, "resolved", None)
            if target is None and getattr(reference, "message_id", None) is not None:
                try:
                    target = await message.channel.fetch_message(int(reference.message_id))
                except Exception:
                    return False
            author = getattr(target, "author", None)
            return author is not None and str(getattr(author, "id", "")) == bot_id

        # A named human address takes precedence over older bot participation.
        vocative = r"[가-힣]{2,6}(?:아|야|님)"
        if (re.search(r"^\\s*" + vocative + r"(?=\\s|[,，:：!?]|$)", content)
                or re.search(r"(?:^|\\s)" + vocative + r"[.!?]?\\s*$", content)):
            return False

        try:
            async for prior in message.channel.history(limit=1, before=message):
                age = (message.created_at - prior.created_at).total_seconds()
                return (0 <= age <= 1800
                        and str(getattr(getattr(prior, "author", None), "id", "")) == bot_id)
        except Exception:
            return False
        return False

'''

ADMISSION_CALL = '''        # burserker-bot-thread-admission-v1: decide before any gateway acknowledgement.
        if not await self._burserker_admit_bot_thread_reply(message):
            return False
'''


def _add_admission_call(source):
    if ADMISSION_CALL in source:
        return source
    tree = ast.parse(source)
    methods = [node for node in ast.walk(tree)
               if isinstance(node, ast.AsyncFunctionDef) and node.name == '_handle_message']
    if len(methods) != 1 or not methods[0].body or not isinstance(methods[0].body[0], ast.Expr):
        raise ValueError('Discord handler changed; review admission before patching')
    doc = methods[0].body[0].value
    if not isinstance(doc, ast.Constant) or not isinstance(doc.value, str):
        raise ValueError('Discord handler docstring changed; review admission before patching')
    lines = source.splitlines(keepends=True)
    lines.insert(methods[0].body[0].end_lineno, ADMISSION_CALL)
    return ''.join(lines)


def patched_source(source):
    if NEW not in source:
        if source.count(OLD) != 1:
            raise ValueError('Discord adapter changed; review mention handling before patching')
        source = source.replace(OLD, NEW, 1)
    if ADMISSION_METHOD not in source:
        source = source.replace(NEW, ADMISSION_METHOD + NEW, 1)
    result = _add_admission_call(source)
    compile(result, '<discord-adapter>', 'exec')
    return result


def install(adapter, backup_root):
    adapter, backup_root = Path(adapter), Path(backup_root)
    if adapter.is_symlink():
        raise ValueError('refusing symlink adapter')
    before = adapter.read_text()
    after = patched_source(before)
    if after == before:
        return None
    stat = adapter.stat()
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = backup_root / ('discord-adapter-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.py')
    shutil.copy2(adapter, backup)
    os.chmod(backup, 0o600)
    temporary = adapter.with_name(adapter.name + '.gujeuk-mention-tmp')
    with temporary.open('x') as output:
        output.write(after)
    os.chmod(temporary, stat.st_mode & 0o777)
    os.chown(temporary, stat.st_uid, stat.st_gid)
    if adapter.read_text() != before:
        temporary.unlink()
        raise ValueError('adapter changed concurrently')
    os.replace(temporary, adapter)
    return backup


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--adapter', required=True)
    parser.add_argument('--backup-root', required=True)
    args = parser.parse_args()
    print('backup:', install(args.adapter, args.backup_root))
