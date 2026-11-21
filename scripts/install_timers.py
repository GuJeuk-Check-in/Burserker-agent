"""One-time owner setup for exactly the Burserker deploy/wiki units."""
from pathlib import Path
import os
import shutil
import subprocess


def main():
    if os.geteuid()!=0:raise SystemExit('Run the one-time unit installation as root')
    root=Path(__file__).resolve().parents[1]
    for name in ('burserker-deploy.service','burserker-deploy.timer','burserker-wiki.service','burserker-wiki.timer'):
        source=root/'deploy/systemd'/name
        target=Path('/etc/systemd/system')/name
        if target.is_symlink():raise ValueError('refusing_symlink_unit')
        shutil.copyfile(source,target);target.chmod(0o644)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now','burserker-deploy.timer','burserker-wiki.timer'],check=True)


if __name__=='__main__':main()
