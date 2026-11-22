"""Public main-only deployment after the exact push SHA passes Agent CI.
No GitHub token, production SSH secret or self-hosted Actions runner is needed.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
import math
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
BASE=Path('/home/ubuntu/gujeuk-pm')


def output(args):return subprocess.check_output(args,text=True).strip()


def save_check(path, data):
    if path.is_symlink():raise ValueError('unsafe_ci_state')
    temp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as handle:
        json.dump(data,handle,allow_nan=False)
        handle.flush();os.fsync(handle.fileno())
    os.replace(temp,path)


def retry_time(headers, checked):
    def number(value, fallback):
        try:
            result=float(value)
            return result if math.isfinite(result) else fallback
        except (TypeError,ValueError):return fallback
    retry=number(headers.get('Retry-After'),0)
    if not retry and headers.get('Retry-After'):
        try:retry=parsedate_to_datetime(headers['Retry-After']).timestamp()-checked
        except (TypeError,ValueError,OverflowError):pass
    reset=number(headers.get('X-RateLimit-Reset'),checked+300)
    return checked+min(86400,max(60,retry,reset-checked+15))


def verified_ci(revision):
    """Persist shared-IP quota backoff across timer ticks and main revisions."""
    path=BASE/'deploy-ci.json'
    if path.is_symlink():raise ValueError('unsafe_ci_state')
    cached=json.loads(path.read_text()) if path.is_file() else {}
    checked=time.time()
    if cached.get('next_check',0)>checked:
        # A successful short-lived receipt is usable only for the exact SHA.
        if cached.get('revision')==revision and cached.get('success') is True:return True
        print(json.dumps({'deployment':cached.get('reason','ci_backoff'),
                          'retry_at':cached['next_check'],'revision':revision}));return False
    url=('https://api.github.com/repos/GuJeuk-Check-in/Burserker-agent/actions/workflows/ci.yml/runs'
         '?branch=main&event=push&per_page=10')
    try:
        with urlopen(Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'BurserkerDeployer'}),timeout=20) as response:
            runs=json.load(response)['workflow_runs']
    except HTTPError as error:
        quota=error.code==429 or (error.code==403 and
            (error.headers.get('X-RateLimit-Remaining')=='0' or error.headers.get('Retry-After')))
        reason='ci_rate_limit_backoff' if quota else 'ci_http_'+str(error.code)
        state={'reason':reason,'next_check':retry_time(error.headers,checked) if quota else checked+900}
        save_check(path,state)
        print(json.dumps({'deployment':reason,'retry_at':state['next_check'],'revision':revision}));return False
    except (URLError,TimeoutError,OSError):
        save_check(path,{'reason':'ci_network_backoff','next_check':checked+120})
        print(json.dumps({'deployment':'ci_network_backoff','revision':revision}));return False
    matches=[r for r in runs if r['head_sha']==revision and r['head_branch']=='main'
             and r['event']=='push' and r['status']=='completed']
    # Do not fall back to an older successful run while a newer run is pending.
    exact=[r for r in runs if r['head_sha']==revision and r['head_branch']=='main' and r['event']=='push']
    success=bool(exact and matches and exact[0]['status']=='completed' and exact[0]['conclusion']=='success')
    save_check(path,{'revision':revision,'success':success,'next_check':checked+120,
                     'reason':'waiting_for_successful_ci','run_id':exact[0]['id'] if exact else None})
    if not success:print(json.dumps({'deployment':'waiting_for_successful_ci','revision':revision}))
    return success


def main():
    expected='https://github.com/GuJeuk-Check-in/Burserker-agent.git'
    origin=output(['git','-C',str(ROOT),'remote','get-url','origin'])
    if origin.rstrip('/') not in {expected,expected.removesuffix('.git')}:
        raise ValueError('unexpected_repository')
    if output(['git','-C',str(ROOT),'status','--porcelain']):
        raise ValueError('dirty_deployment_checkout_not_overwritten')
    subprocess.run(['git','-C',str(ROOT),'fetch','--quiet','origin','main'],check=True,timeout=60)
    revision=output(['git','-C',str(ROOT),'rev-parse','origin/main'])
    state=BASE/'deployed.json'
    if state.is_file() and json.loads(state.read_text()).get('revision')==revision:return
    if not verified_ci(revision):return
    subprocess.run(['git','-C',str(ROOT),'checkout','--detach',revision],check=True,timeout=30)
    subprocess.run([sys.executable,str(ROOT/'scripts/deploy.py'),'--revision',revision],check=True,timeout=900)


if __name__=='__main__':main()
