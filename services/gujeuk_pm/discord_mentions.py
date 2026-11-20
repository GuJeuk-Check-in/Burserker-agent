"""Narrow compatibility patch for Hermes' Discord managed-bot-role mentions.

Only the bot's own managed role counts. User/role authorization, bot-to-bot raw
mention rules and unrelated role mentions are unchanged. Reapply to a rebuilt
Hermes image only after the exact upstream method passes the compatibility check.
"""
from __future__ import annotations

import argparse
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


def patched_source(source):
    if NEW in source:
        return source
    if source.count(OLD) != 1:
        raise ValueError('Discord adapter changed; review mention handling before patching')
    result = source.replace(OLD, NEW, 1)
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
