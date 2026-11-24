"""Host-only retention. Never prune volumes, containers, bases, or unknown images."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from datetime import datetime

BASE = Path('/var/lib/gujeuk-image-retention')
DEPLOY = Path('/home/ubuntu/gujeuk-pm/.deploy.lock')
CHANNELS = {
    'gujeuk-pm-hermes': r'gujeuk-pm-hermes:[a-f0-9]{40}',
    'beopdong-app-stag': r'ghcr\.io/gujeuk-check-in/beopdong-api:stag-[a-f0-9]{40}',
    'gujeuk-app-stag': r'ghcr\.io/gujeuk-check-in/gujeuk-check-in-server:stag-[a-f0-9]{40}',
    'gujeuk-app-prod': r'ghcr\.io/gujeuk-check-in/gujeuk-check-in-server:prod-[a-f0-9]{40}',
    'gujuck-game-app': r'ghcr\.io/gujeuk-check-in/gujeuk-game-fe-server:[a-f0-9]{7,40}',
}


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def snapshot():
    ids = docker('ps', '-aq', '--no-trunc').split()
    fmt = '{{.Name}} {{.Id}} {{.Image}} {{.State.Status}} {{.State.StartedAt}} {{.RestartCount}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{json .Mounts}}'
    rows = {}
    for line in docker('container', 'inspect', '-f', fmt, *ids).splitlines():
        name, cid, image, status, start, restart, health, mounts = line.split(' ', 7)
        rows[name.lstrip('/')] = [cid, image, status, start, restart, health, sorted(json.dumps(m, sort_keys=True) for m in json.loads(mounts))]
    return rows, sorted(docker('volume', 'ls', '-q').splitlines())


def inventory():
    images = {}
    for row in docker('image', 'ls', '--no-trunc', '--format', '{{.ID}} {{.Repository}}:{{.Tag}}').splitlines():
        key, tag = row.split(); images.setdefault(key, set()).add(tag)
    return images


def candidates(history, rows, images, now):
    used = {v[1] for v in rows.values()}; result = set()
    for name, pattern in CHANNELS.items():
        if name not in rows: continue
        _, current, status, start, _, health, _ = rows[name]
        if status != 'running' or health not in ('none','healthy'): continue
        if now - datetime.fromisoformat(start.replace('Z','+00:00')[:19]+'+00:00').timestamp() < 3600: continue
        if not images.get(current) or not all(re.fullmatch(pattern,t) for t in images[current]): continue
        for old in history.get(name,[]):
            if old not in used and images.get(old) and all(re.fullmatch(pattern,t) for t in images[old]): result.add(old)
    return result


def main():
    p=argparse.ArgumentParser(); p.add_argument('--initial', nargs='*', default=None); p.add_argument('--apply',action='store_true'); a=p.parse_args()
    os.umask(0o077); BASE.mkdir(exist_ok=True)
    lock=DEPLOY.open('r'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    state=BASE/'observed.json'; hist=json.loads(state.read_text()) if state.exists() else {}; rows,vols=snapshot(); images=inventory(); free=shutil.disk_usage('/').free
    ids=candidates(hist,rows,images,time.time())
    if a.initial is not None:
        ids=set(a.initial); used={v[1] for v in rows.values()}
        for i in ids:
            if not re.fullmatch(r'sha256:[a-f0-9]{64}',i) or i in used or not images.get(i) or not any(all(re.fullmatch(pat,t) for t in images[i]) for pat in [*CHANNELS.values(),r'gujeuk-pm-worker:local']): raise RuntimeError('initial plan changed')
    print('candidates', sorted(ids),flush=True)
    if not a.apply: return
    for i in sorted(ids):
        if snapshot()!=(rows,vols) or inventory()!=images: raise RuntimeError('inventory changed')
        docker('image','rm','--no-prune',i); del images[i]
    if ids or a.initial is not None:
        if snapshot()!=(rows,vols): raise RuntimeError('container state changed')
        print(docker('builder','prune','--all','--force').splitlines()[-1:])
    if snapshot()!=(rows,vols) or inventory()!=images: raise RuntimeError('postconditions changed')
    for name in CHANNELS:
        seen=set(hist.get(name,[])) & images.keys()
        if name in rows: seen.add(rows[name][1])
        hist[name]=sorted(seen)
    tmp=state.with_suffix('.tmp'); tmp.write_text(json.dumps(hist)); tmp.replace(state)
    print('verified containers',len(rows),'volumes',len(vols),'images',len(images),'free_delta_bytes',shutil.disk_usage('/').free-free)


if __name__=='__main__': main()
