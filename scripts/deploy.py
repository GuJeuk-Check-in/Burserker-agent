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


def compose(release, image):
    env=os.environ | {'BURSERKER_CODE':str(release),'BURSERKER_HERMES_IMAGE':image}
    args=['docker','compose','-p','gujeuk-pm','--project-directory',str(BASE),
        '-f',str(release/'deploy/imported/compose.yaml'),'-f',str(release/'deploy/imported/compose.pm.yaml'),
        '-f',str(release/'compose.agent.yaml')]
    return args,env


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
    args=parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}',args.revision):raise ValueError('invalid_revision')
    repo=Path(__file__).resolve().parents[1]
    if not (BASE/'data').is_dir():raise ValueError('existing_production_state_required')
    if shutil.disk_usage(BASE).free<2*1024**3:raise ValueError('low_disk_no_automatic_cleanup')
    os.umask(0o077)
    with (BASE/'.deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        release=prepare_release(repo,args.revision)
        image='gujeuk-pm-hermes:'+args.revision
        # SQLite backup API produces consistent, verified snapshots before migration.
        run(['docker','exec','gujeuk-pm-pm-worker-1','python','-B','-m','oijwef098234.gujeuk_ai.ops','backup',
            '--state','/state','--discord-state','/discord','--collector','/collector/collector.py'])
        run(['docker','build','-f',release/'services/gujeuk_pm/Dockerfile.hermes','-t',image,release])
        if args.build_worker:
            run(['docker','build','-f',release/'services/gujeuk_pm/Dockerfile','-t','gujeuk-pm-worker:local',release])
        secret=BASE/'data/pm-figma';secret.mkdir(mode=0o700,exist_ok=True);secret.chmod(0o700)
        queue=BASE/'data/pm-workspace/notion-read';queue.mkdir(mode=0o700,parents=True,exist_ok=True)
        # No token in policy input, argv, logs or the Git release.
        run(['docker','exec','-i','--user','hermes','-e','HOME=/opt/data','gujeuk-pm-hermes',
             '/opt/hermes/.venv/bin/python','-'],input=(release/'scripts/gateway_policy.py').read_bytes())
        command,env=compose(release,image)
        run(command+['config','--quiet'],env=env)
        # Only these agent containers: other production applications are never targeted.
        run(command+['up','-d','--no-build','--no-deps','--force-recreate','hermes','collector','pm-worker'],env=env)
        # Preserve the existing notion-reader project/network and its data directory.
        notion=release/'deploy/compose.notion.yaml'
        ncommand=['docker','compose','-p','gujeuk-pm-notion-read','--project-directory',str(BASE),'-f',str(notion)]
        run(ncommand+['config','--quiet'],env=env)
        run(ncommand+['up','-d','--no-build','--force-recreate'],env=env)
        metadata={'revision':args.revision,'release':str(release),'hermes_image':image,'deployed_at':time.time()}
        (BASE/'deployed.json').write_text(json.dumps(metadata,indent=2)+'\n')
        print(json.dumps(metadata))


if __name__=='__main__':main()
