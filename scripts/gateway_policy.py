"""Owner-only policy migration: preserve unrelated profile settings and secrets."""
from pathlib import Path
from datetime import datetime, timezone
import os
import shutil
import hermes_yaml

path=Path('/opt/data/config.yaml')
before=path.read_text();stat=path.stat()
yaml=hermes_yaml.YAML();config=yaml.load(before)
config.setdefault('discord',{})['require_mention']=True
config['discord']['thread_require_mention']=False
config.setdefault('display',{})['busy_input_mode']='queue'
config['max_concurrent_sessions']=4
# Threads remain shared across participants; busy requests get separate trusted lanes.
backup=Path('/opt/data/backups/burserker-git-policy');backup.mkdir(mode=0o700,parents=True,exist_ok=True)
snapshot=backup/('config-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.yaml')
shutil.copy2(path,snapshot);snapshot.chmod(0o600)
temp=path.with_name('config.burserker.'+str(os.getpid())+'.tmp')
with temp.open('x') as handle:yaml.dump(config,handle)
temp.chmod(stat.st_mode & 0o777)
os.chown(temp,stat.st_uid,stat.st_gid)
if path.read_text()!=before:raise RuntimeError('concurrent_config_change')
os.replace(temp,path)
print('mention=true thread_followup_without_mention=true busy=queue max_concurrent_sessions=4')
