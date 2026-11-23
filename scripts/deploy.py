"""Deploy only agent services from an immutable Git release; never deletes volumes."""
from pathlib import Path
import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import tarfile
import time

BASE=Path('/home/ubuntu/gujeuk-pm')


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def compose(release, image, *, worker_image='gujeuk-pm-worker:local', services=None):
    env=os.environ | {'BURSERKER_CODE':str(release),'BURSERKER_HERMES_IMAGE':image,
                      'BURSERKER_WORKER_IMAGE':worker_image}
    for name,key in [('hermes','HERMES'),('collector','COLLECTOR'),('pm-worker','WORKER'),('notion-reader','READER')]:
        env['BURSERKER_'+key+'_CODE']=str((services or {}).get(name,{}).get('release',release))
    args=['docker','compose','-p','gujeuk-pm','--project-directory',str(BASE),
        '-f',str(release/'deploy/imported/compose.yaml'),'-f',str(release/'deploy/imported/compose.pm.yaml'),
        '-f',str(release/'compose.agent.yaml')]
    return args,env


def gateway_status():
    # Read only metadata, never profiles, credentials or message contents.
    code = ('import json;from pathlib import Path;p=Path("/opt/data/gateway_state.json");'
            'd=json.loads(p.read_text());print(json.dumps({"active":d.get("active_agents"),'
            '"gateway":d.get("gateway_state"),"discord":d.get("platforms",{}).get("discord",{}).get("state"),'
            '"modified":p.stat().st_mtime}))')
    result=run(['docker','exec','--user','hermes','gujeuk-pm-hermes','/opt/hermes/.venv/bin/python','-c',code],
               capture_output=True,text=True,timeout=10)
    return json.loads(result.stdout)


def gateway_idle():
    try:
        status=gateway_status()
        jobs = [run(['systemctl','show',name,'-p','ActiveState','--value'],
                    capture_output=True,text=True,timeout=5).stdout.strip()
                for name in ('burserker-wiki.service', 'gujeuk-pm-analysis.service', 'gujeuk-pm-backup.service')]
        return (type(status['active']) is int and status['active']==0
                and all(state in {'inactive','failed'} for state in jobs))
    except (subprocess.SubprocessError,ValueError,KeyError,OSError):
        return False


def wait_ready(targets, started, *, timeout=60):
    names={'hermes':'gujeuk-pm-hermes','collector':'gujeuk-pm-collector-1',
           'pm-worker':'gujeuk-pm-pm-worker-1','notion-reader':'gujeuk-pm-notion-reader'}
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            state=run(['docker','inspect','--format',
                '{{.State.Running}} {{.State.Restarting}} {{.State.OOMKilled}}',
                *[names[name] for name in targets]],capture_output=True,text=True,timeout=10)
            ready=state.stdout.splitlines()==['true false false']*len(targets)
            if ready and 'hermes' in targets:
                gateway=gateway_status()
                ready=(gateway['modified']>=started and gateway['gateway']=='running'
                       and gateway['discord']=='connected')
            if ready:
                return
        except (subprocess.SubprocessError,ValueError,KeyError,OSError):
            pass
        time.sleep(1)
    raise RuntimeError('agent_readiness_failed_metadata_not_advanced')


def prepare_release(repo, revision):
    target=BASE/'releases'/revision
    if target.is_dir():
        if (target/'.release-sha').read_text().strip()!=revision:
            raise ValueError('release_integrity_marker_missing')
        return target
    temp=target.with_name(revision+'.tmp.'+str(os.getpid()))
    temp.mkdir(mode=0o700,parents=True)
    archive=run(['git','-C',repo,'archive',revision],capture_output=True).stdout
    import io
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for member in bundle.getmembers():
            name=Path(member.name)
            if member.issym() or member.islnk() or name.is_absolute() or '..' in name.parts or not (member.isdir() or member.isfile()):
                raise ValueError('unsafe_release_archive')
        bundle.extractall(temp)
    (temp/'.release-sha').write_text(revision+'\n')
    os.replace(temp,target)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',required=True)
    parser.add_argument('--build-worker',action='store_true')
    parser.add_argument('--plan',action='store_true',help='Prepare an immutable release and print the plan; no Docker/runtime changes')
    args=parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}',args.revision):raise ValueError('invalid_revision')
    repo=Path(__file__).resolve().parents[1]
    if not (BASE/'data').is_dir():raise ValueError('existing_production_state_required')

    os.umask(0o077)
    with (BASE/'.deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        release=prepare_release(repo,args.revision)
        from deploy_plan import make_plan
        state=BASE/'deployed.json'
        if state.is_symlink():raise ValueError('unsafe_deployment_state')
        previous=json.loads(state.read_text()) if state.is_file() else {}
        plan=make_plan(release,previous,force_worker=args.build_worker)
        print(json.dumps({'revision':args.revision,'rollout_plan':plan}),flush=True)
        if args.plan:return
        if plan.get('topology') == 'single':
            from single_deploy import deploy_single
            deploy_single(release, previous, plan, base=BASE, run=run,
                          idle=gateway_idle, ready=wait_ready)
            return
        image=plan['images']['hermes']['image']
        worker_image=plan['images']['worker']['image']
        if plan['targets'] and shutil.disk_usage(BASE).free<2*1024**3:
            raise ValueError('low_disk_no_automatic_cleanup')
        if 'hermes' in plan['targets'] and not gateway_idle():
            print(json.dumps({'deployment':'deferred_until_gateway_and_wiki_idle','revision':args.revision}))
            return
        if plan['targets']:
            command,env=compose(release,image,worker_image=worker_image,services=plan['services'])
            ncommand=['docker','compose','-p','gujeuk-pm-notion-read','--project-directory',str(BASE),
                      '-f',str(release/'deploy/compose.notion.yaml')]
            run(command+['config','--quiet'],env=env)
            run(ncommand+['config','--quiet'],env=env)
            # Consistent SQLite backups precede any runtime migration.
            run(['docker','exec','gujeuk-pm-pm-worker-1','python','-B','-m','oijwef098234.gujeuk_ai.ops','backup',
                '--state','/state','--discord-state','/discord','--collector','/collector/collector.py'])
            if 'hermes' in plan['builds']:
                run(['docker','build','-f',release/'services/gujeuk_pm/Dockerfile.hermes','-t',image,release])
            if 'worker' in plan['builds']:
                run(['docker','build','-f',release/'services/gujeuk_pm/Dockerfile','-t',worker_image,release])
            secret=BASE/'data/pm-figma';secret.mkdir(mode=0o700,exist_ok=True);secret.chmod(0o700)
            queue=BASE/'data/pm-workspace/notion-read';queue.mkdir(mode=0o700,parents=True,exist_ok=True)
            # A question or wiki run can start while an image is building.
            if 'hermes' in plan['targets'] and not gateway_idle():
                print(json.dumps({'deployment':'deferred_until_gateway_and_wiki_idle','revision':args.revision}))
                return
            if 'hermes' in plan['targets']:
                run(['docker','exec','-i','--user','hermes','-e','HOME=/opt/data','gujeuk-pm-hermes',
                     '/opt/hermes/.venv/bin/python','-'],input=(release/'scripts/gateway_policy.py').read_bytes())
            started=time.time()
            targets=[name for name in plan['targets'] if name!='notion-reader']
            if targets:
                run(command+['up','-d','--no-build','--no-deps',*targets],env=env)
            if 'notion-reader' in plan['targets']:
                run(ncommand+['up','-d','--no-build','--no-deps','notion-reader'],env=env)
            wait_ready(plan['targets'],started)
        metadata={'revision':args.revision,'release':str(release),'hermes_image':image,
                  'worker_image':worker_image,'deployed_at':time.time(),**plan}
        temp=BASE/('deployed.'+str(os.getpid())+'.tmp')
        with temp.open('x') as handle:
            handle.write(json.dumps(metadata,indent=2)+'\n')
            handle.flush();os.fsync(handle.fileno())
        os.replace(temp,state)
        print(json.dumps(metadata))


if __name__=='__main__':main()
