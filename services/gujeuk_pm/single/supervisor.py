"""Supervise deterministic PM children under s6; never logs credentials/bodies."""
import argparse
import json
import os
from pathlib import Path
import pwd
import signal
import subprocess
import time

STATUS = Path('/run/burserker-status.json')
SOURCE = '/release/src'


def process_identity(pid):
    try:
        fields = Path('/proc/' + str(pid) + '/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def health(path=STATUS, *, now=None):
    try:
        data = json.loads(path.read_text())
        alive = (time.time() if now is None else now) - data['checked'] < 15
        children = data['children']
        alive = alive and set(children) == {'collector', 'worker', 'reader'}
        alive = alive and all(c['identity'] is not None and
            process_identity(c['pid']) == c['identity'] for c in children.values())
        return {'healthy': bool(alive), 'children': children}
    except (OSError, ValueError, KeyError, TypeError):
        return {'healthy': False, 'children': {}}


def specs():
    user = pwd.getpwnam('hermes')
    environment = {'PATH': '/opt/pm/bin:/usr/bin:/bin', 'PYTHONPATH': SOURCE,
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
        'PLAYWRIGHT_BROWSERS_PATH': '/opt/browsers', 'GUJEUK_READER_SHARED_QUEUE': '1'}
    common = ['/opt/pm/bin/python', '-B']
    commands = {
        'collector': common + ['/collector/collector.py', '--guild', '1413450780068937790',
            '--state', '/opt/data/pm-discord', '--token-file', '/run/secrets/discord_token',
            '--interval', '120', '--all-history'],
        'worker': common + ['-m', 'oijwef098234.gujeuk_ai.runtime', 'worker',
            '--state', '/opt/data/pm-workspace', '--discord-state', '/opt/data/pm-discord',
            '--collector', '/collector/collector.py', '--interval', '120'],
        'reader': common + ['-m', 'oijwef098234.gujeuk_ai.notion_reader', '--state', '/requests'],
    }
    return {name: {'command': command, 'uid': 1001 if name == 'reader' else user.pw_uid,
        'gid': 1001 if name == 'reader' else user.pw_gid,
        'env': environment | {'HOME': '/tmp/pm-' + name}}
        for name, command in commands.items()}


def prepare_queue():
    directory = Path('/requests')
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('queue_mount_required')
    os.chown(directory, pwd.getpwnam('hermes').pw_uid, 1002)
    directory.chmod(0o2770)
    for pattern in ('*.request.json', '*.response.json'):
        for file in directory.glob(pattern):
            if file.is_symlink() or not file.is_file():
                raise ValueError('invalid_queue_file')
            os.chown(file, -1, 1002)
            file.chmod(0o660)


class Supervisor:
    def __init__(self, children, path=STATUS):
        self.specifications, self.path = children, path
        self.children, self.restarts, self.retry = {}, {}, {}
        self.stopping = False

    def tick(self):
        for name, spec in self.specifications.items():
            process = self.children.get(name)
            if process is not None and process.poll() is not None:
                # Reap descendants too; a crashed browser wrapper must not leak Chromium.
                self.signal_group(process, signal.SIGKILL)
                self.children.pop(name)
                self.restarts[name] = self.restarts.get(name, 0) + 1
                self.retry[name] = time.monotonic() + min(30, 2 ** min(self.restarts[name], 5))
                print(json.dumps({'child_exit': name, 'restarts': self.restarts[name]}), flush=True)
            if name not in self.children and time.monotonic() >= self.retry.get(name, 0):
                options = {'env': spec['env'], 'start_new_session': True, 'stdin': subprocess.DEVNULL}
                if 'uid' in spec:
                    options.update(user=spec['uid'], group=spec['gid'], extra_groups=[1002], umask=0o077)
                    home = Path(spec['env']['HOME'])
                    home.mkdir(mode=0o700, exist_ok=True)
                    if home.is_symlink():
                        raise ValueError('invalid_child_home')
                    os.chown(home, spec['uid'], spec['gid'], follow_symlinks=False)
                self.children[name] = subprocess.Popen(spec['command'], **options)
        data = {'checked': time.time(), 'children': {name: {
            'pid': p.pid, 'identity': process_identity(p.pid), 'restarts': self.restarts.get(name, 0)}
            for name, p in self.children.items()}}
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data))
        temporary.chmod(0o644)
        temporary.replace(self.path)

    @staticmethod
    def signal_group(process, sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    def stop(self):
        self.stopping = True
        for process in self.children.values():
            self.signal_group(process, signal.SIGTERM)
        deadline = time.monotonic() + 20
        for process in self.children.values():
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
            self.signal_group(process, signal.SIGKILL)
            process.wait()
        self.path.unlink(missing_ok=True)

    def run(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: setattr(self, 'stopping', True))
        try:
            while not self.stopping:
                self.tick()
                time.sleep(1)
        finally:
            self.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--health', action='store_true')
    args = parser.parse_args()
    if args.health:
        result = health()
        print(json.dumps(result))
        raise SystemExit(0 if result['healthy'] else 1)
    os.umask(0o077)
    prepare_queue()
    Supervisor(specs()).run()


if __name__ == '__main__':
    main()
