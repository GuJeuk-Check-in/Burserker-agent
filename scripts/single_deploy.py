"""One-container rollout, quiesced SQLite backup and bounded rollback; no volume removal."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

LEGACY = ['gujeuk-pm-collector-1', 'gujeuk-pm-pm-worker-1', 'gujeuk-pm-notion-reader']
GATEWAY = 'gujeuk-pm-hermes'


def single_compose(base, release, service):
    env = os.environ | {'BURSERKER_CODE': str(release),
        'BURSERKER_HERMES_CODE': service['release'], 'BURSERKER_HERMES_IMAGE': service['image']}
    return ['docker', 'compose', '-p', 'gujeuk-pm', '--project-directory', str(base),
            '-f', str(release / 'compose.single.yaml')], env


def supervised(run, *, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = run(['docker', 'exec', GATEWAY, '/opt/hermes/.venv/bin/python',
                          '/opt/burserker/supervisor.py', '--health'], capture_output=True, text=True, timeout=10)
            if json.loads(result.stdout)['healthy']:
                return
        except (subprocess.SubprocessError, KeyError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError('supervised_children_not_ready')


def write_state(base, state):
    path = base / 'deployed.json'
    temporary = base / ('deployed.' + str(os.getpid()) + '.tmp')
    with temporary.open('x') as output:
        output.write(json.dumps(state, indent=2) + '\n')
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def rollback(base, previous, run):
    release = Path(previous['release'])
    if previous.get('topology') == 'single':
        command, env = single_compose(base, release, previous['services']['hermes'])
        run(command + ['up', '-d', '--no-build', '--no-deps', 'hermes'], env=env)
    else:
        from deploy import compose
        command, env = compose(release, previous['hermes_image'],
            worker_image=previous.get('worker_image', 'gujeuk-pm-worker:local'),
            services=previous.get('services'))
        run(command + ['up', '-d', '--no-build', '--no-deps', 'hermes'], env=env)
        run(['docker', 'start', *LEGACY])


def deploy_single(release, previous, plan, *, base, run, idle, ready):
    if plan.get('topology') != 'single':
        raise ValueError('single_plan_required')
    image = plan['images']['hermes']['image']
    metadata = {'revision': release.name, 'release': str(release), 'hermes_image': image,
                'deployed_at': time.time(), **plan}
    if not plan['targets']:
        write_state(base, metadata)
        return
    required = (3 if plan['builds'] else 1) * 1024 ** 3
    if shutil.disk_usage(base).free < required:
        raise ValueError('low_disk_no_automatic_cleanup')
    if not idle():
        print(json.dumps({'deployment': 'deferred_until_gateway_and_wiki_idle'}))
        return
    command, env = single_compose(base, release, plan['services']['hermes'])
    run(command + ['config', '--quiet'], env=env)
    if plan['builds']:
        run(['docker', 'build', '-f', release / 'services/gujeuk_pm/Dockerfile.single', '-t', image, release])
    if not idle():
        print(json.dumps({'deployment': 'deferred_until_gateway_and_wiki_idle'}))
        return
    history = base / 'deployment-history'
    history.mkdir(mode=0o700, exist_ok=True)
    checkpoint = history / ('before-single-' + release.name + '.json')
    if not checkpoint.exists():
        checkpoint.write_text(json.dumps(previous, indent=2))
        checkpoint.chmod(0o600)
    migrating = previous.get('topology') != 'single'
    # Freeze all old writers before snapshot and before starting ANY new writer.
    names = [GATEWAY] + (LEGACY if migrating else [])
    try:
        run(['docker', 'stop', '-t', '60', *names])
        run(['python3', '-m', 'oijwef098234.gujeuk_ai.ops', 'backup',
             '--state', base / 'data/pm-workspace', '--discord-state', base / 'data/pm-discord',
             '--collector', release / 'services/gujeuk_pm/collector.py'],
            env=os.environ | {'PYTHONPATH': str(release / 'src')})
        started = time.time()
        run(command + ['up', '-d', '--no-build', '--no-deps', 'hermes'], env=env)
        ready(['hermes'], started, timeout=120)
        supervised(run)
    except Exception:
        # Old images/source/state are retained. Do not restore an old DB over new writes.
        rollback(base, previous, run)
        ready(['hermes'], time.time() - 120, timeout=120)
        raise
    write_state(base, metadata)
    print(json.dumps({'deployment': 'single_ready', 'revision': release.name,
                      'legacy_containers': 'stopped_for_verification' if migrating else 'unchanged'}))
