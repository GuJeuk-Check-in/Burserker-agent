"""Public main-only deployment after the exact push SHA passes Agent CI.
No GitHub token, production SSH secret or self-hosted Actions runner is needed.
"""
from pathlib import Path
import json
import subprocess
import sys
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
BASE=Path('/home/ubuntu/gujeuk-pm')


def output(args):return subprocess.check_output(args,text=True).strip()


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
    url='https://api.github.com/repos/GuJeuk-Check-in/Burserker-agent/actions/workflows/ci.yml/runs?branch=main&event=push&per_page=10'
    with urlopen(Request(url,headers={'Accept':'application/vnd.github+json','User-Agent':'BurserkerDeployer'}),timeout=20) as response:
        runs=json.load(response)['workflow_runs']
    matches=[r for r in runs if r['head_sha']==revision and r['head_branch']=='main' and r['event']=='push']
    if not matches or matches[0]['conclusion']!='success':
        print(json.dumps({'deployment':'waiting_for_successful_ci','revision':revision}));return
    subprocess.run(['git','-C',str(ROOT),'checkout','--detach',revision],check=True,timeout=30)
    subprocess.run([sys.executable,str(ROOT/'scripts/deploy.py'),'--revision',revision],check=True,timeout=900)


if __name__=='__main__':main()
