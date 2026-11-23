"""Host-side supervision of PM services only. Reports contain no team documents."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

ROOT=Path('/home/ubuntu/gujeuk-pm')
COMPOSE=['docker','compose','-f','compose.yaml','-f','compose.pm.yaml']


def run(command,timeout=90):
    return subprocess.run(command,cwd=ROOT,text=True,capture_output=True,timeout=timeout)


def notify(message):
    result=run(COMPOSE+['exec','-T','hermes','/opt/hermes/bin/hermes','send',
                       '--to','discord:1572130653506371645','--quiet',message])
    if result.returncode:
        raise ValueError('owner_report_delivery_failed')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['health','backup'])
    args=parser.parse_args()
    os.umask(0o077)
    deployed = ROOT/'deployed.json'
    single = deployed.is_file() and json.loads(deployed.read_text()).get('topology') == 'single'
    if single:
        operation = ['docker','exec','--user','hermes','-e','HOME=/opt/data',
            '-e','PYTHONPATH=/opt/data/workspace/gujeuk-pm/src','gujeuk-pm-hermes',
            '/opt/pm/bin/python','-B','-m','oijwef098234.gujeuk_ai.ops',args.mode,
            '--state','/opt/data/pm-workspace','--discord-state','/opt/data/pm-discord',
            '--collector','/collector/collector.py']
    else:
        operation = COMPOSE+['exec','-T','pm-worker','python','-m','oijwef098234.gujeuk_ai.ops',args.mode,
            '--state','/state','--discord-state','/discord','--collector','/collector/collector.py']
    result=run(operation,timeout=300)
    try:
        data=json.loads(result.stdout)
    except (ValueError,TypeError):
        data={'healthy':False,'issues':['pm-worker:unavailable']}
    if args.mode=='backup':
        if result.returncode or data.get('issues'):
            notify('구즉 PM 백업 실패: '+', '.join(data.get('issues',['backup_failed']))+'. 운영 데이터는 삭제하지 않았어요.')
            raise ValueError('backup_failed')
        print(json.dumps(data))
        return
    issues=data.get('issues',[])
    if single:
        supervisor=run(['docker','exec','gujeuk-pm-hermes','/opt/hermes/.venv/bin/python',
                        '/opt/burserker/supervisor.py','--health'])
        if supervisor.returncode:
            issues.append('single:children_not_ready')
    ps=run(COMPOSE+['ps','--format','json','collector','pm-worker','hermes'])
    states={}
    if ps.returncode==0:
        for line in ps.stdout.splitlines():
            row=json.loads(line)
            states[row['Service']]=row.get('State')
    for name in (('hermes',) if single else ('collector','pm-worker','hermes')):
        if states.get(name)!='running':
            issues.append(name+':not_running')
    analysis=run(['systemctl','show','gujeuk-pm-analysis.service','-p','ExecMainStatus','--value'])
    if analysis.returncode==0 and analysis.stdout.strip() not in {'','0'}:
        issues.append('analysis:last_run_failed')
    issues=sorted(set(issues))
    target=ROOT/'maintenance/health-state.json'
    if target.is_symlink():
        raise ValueError('health_state_symlink_rejected')
    previous=json.loads(target.read_text()) if target.exists() else {}
    changed=issues!=previous.get('issues',[])
    reminder=bool(issues) and time.time()-previous.get('notified_at',0)>3600
    notified_at=previous.get('notified_at',0)
    if changed or reminder:
        notify(('구즉 PM 운영 점검: '+', '.join(issues)+'. 수집·분석 상태를 확인해 주세요.') if issues
               else '구즉 PM 수집·분석 서비스가 복구됐어요.')
        notified_at=time.time()
    temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'issues':issues,'notified_at':notified_at,'checked':time.time()}))
    temporary.chmod(0o600); temporary.replace(target)
    print(json.dumps({'healthy':not issues,'issues':issues,'gaps':data.get('gaps',{})}))


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'maintenance_error':type(error).__name__}))
        raise SystemExit(1) from None
