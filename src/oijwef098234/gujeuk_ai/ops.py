"""Private SQLite snapshots and bounded freshness checks; never deletes source data."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import time
from .workspace import Workspace


def health(status, collector, *, checked=None):
    checked = time.time() if checked is None else checked
    issues, gaps = [], {}
    for kind in ('notion', 'discord', 'github'):
        source = status['sources'][kind]
        coverage = source['coverage']
        try:
            age = checked - datetime.fromisoformat(coverage.get('last_success', coverage.get('checked'))).timestamp()
        except (KeyError, ValueError, TypeError):
            age = float('inf')
        if not source['documents']:
            issues.append(kind + ':no_documents')
        if age > 3600:
            issues.append(kind + ':stale')
        gaps[kind] = len(coverage.get('issues', []))
    if checked - collector.get('finished', 0) > 900:
        issues.append('discord:collector_stale')
    gaps['discord_permissions'] = len(collector.get('issues', []))
    return {'healthy': not issues, 'issues': sorted(issues), 'gaps': gaps}


def backup(store, discord_store, root):
    if root.is_symlink():
        raise ValueError('backup directory symlink rejected')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sum(p.stat().st_size for p in root.rglob('*.db')) >= 1024 ** 3:
        raise ValueError('backup_capacity_limit_no_automatic_deletion')
    if shutil.disk_usage(root).free < 1024 ** 3:
        raise ValueError('low_disk_no_backup')
    target = root / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target.mkdir(mode=0o700)
    store.backup_to(target/'workspace.db')
    discord_store.backup_to(target/'discord.db')
    from .messaging import MessageStore
    outbox = MessageStore.backup_existing(root.parent/'discord-outbox.db', target/'discord-outbox.db')
    return {'backup': target.name, 'verified_databases': 2 + int(outbox)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['health','backup'])
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--discord-state',type=Path,required=True)
    parser.add_argument('--collector',type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    store = discord_store = None
    try:
        if not (args.state/'workspace.db').is_file():
            raise ValueError('workspace_unavailable')
        store=Workspace(args.state/'workspace.db')
        if args.mode=='health':
            collector=json.loads((args.discord_state/'coverage.json').read_text())
            result=health(store.source_status(),collector)
        else:
            spec=importlib.util.spec_from_file_location('gujeuk_backup_collector',args.collector)
            if spec is None or spec.loader is None:
                raise ValueError('collector_module_unavailable')
            module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            discord_store=module.Store(args.discord_state,'1413450780068937790',read_only=True)
            result=backup(store,discord_store,args.state/'backups')
        print(json.dumps(result))
    except Exception as error:
        print(json.dumps({'healthy':False,'issues':['maintenance:'+type(error).__name__]}))
        raise SystemExit(1) from None
    finally:
        if discord_store is not None:
            discord_store.db.close()
        if store is not None:
            store.close()


if __name__=='__main__':
    main()
