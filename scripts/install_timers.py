"""One-time owner setup for exactly the Burserker deploy/wiki units."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--enable-schedules',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit('Run the one-time unit installation as root')
    root=Path(__file__).resolve().parents[1]
    for name in ('burserker-deploy.service','burserker-deploy.timer','burserker-wiki.service','burserker-wiki.timer',
                 'gujeuk-pm-health.service','gujeuk-pm-backup.service',
                 'gujeuk-pm-dispatch.service','gujeuk-pm-dispatch.timer'):
        source=root/'deploy/systemd'/name
        target=Path('/etc/systemd/system')/name
        if target.is_symlink():raise ValueError('refusing_symlink_unit')
        shutil.copyfile(source,target);target.chmod(0o644)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now','burserker-deploy.timer','burserker-wiki.timer'],check=True)
    if args.enable_schedules:
        subprocess.run(['systemctl','enable','--now','gujeuk-pm-dispatch.timer'],check=True)


if __name__=='__main__':main()
