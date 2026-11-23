"""Offline consolidation regressions, never production Docker or Discord."""
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import shutil
import signal
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = spec_from_file_location('single_test_' + Path(path).stem, ROOT / path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


planner = load('scripts/deploy_plan.py')
supervisor = load('services/gujeuk_pm/single/supervisor.py')
deployment = load('scripts/single_deploy.py')


def test_single_plan_migrates_once_and_preserves_doc_only_runtime(tmp_path):
    a, b = tmp_path/'a', tmp_path/'b'
    a.mkdir()
    (a/'compose.single.yaml').write_text('one')
    plan = planner.make_plan(a, {})
    assert plan['targets'] == plan['builds'] == ['hermes']
    assert set(plan['services']) == {'hermes'}
    shutil.copytree(a, b)
    (b/'README.md').write_text('docs only')
    previous = plan | {'release': str(a)}
    next_plan = planner.make_plan(b, previous)
    assert next_plan['targets'] == next_plan['builds'] == []
    assert next_plan['services']['hermes']['release'] == str(a)
    (b/'uv.lock').write_text('new dependency')
    assert planner.make_plan(b, previous)['builds'] == ['hermes']


def test_shared_queue_files_remain_group_readable_under_private_umask(tmp_path, monkeypatch):
    from oijwef098234.gujeuk_ai.notion_reader import write_new
    monkeypatch.setenv('GUJEUK_READER_SHARED_QUEUE', '1')
    old = os.umask(0o077)
    try:
        write_new(tmp_path/'queue.json', {'ok': True})
    finally:
        os.umask(old)
    assert (tmp_path/'queue.json').stat().st_mode & 0o777 == 0o660
    monkeypatch.delenv('GUJEUK_READER_SHARED_QUEUE')
    write_new(tmp_path/'private.json', {'ok': True})
    assert (tmp_path/'private.json').stat().st_mode & 0o777 == 0o600


def test_supervisor_restarts_child_and_stops_all_process_groups(tmp_path):
    specs = {name: {'command': [sys.executable, '-c', 'import time;time.sleep(120)'],
                    'env': dict(os.environ)} for name in ('collector', 'worker', 'reader')}
    instance = supervisor.Supervisor(specs, tmp_path/'status.json')
    try:
        instance.tick()
        old = instance.children['reader']
        old.kill()
        old.wait()
        instance.tick()
        assert 'reader' not in instance.children
        assert instance.restarts['reader'] == 1
        instance.retry['reader'] = 0
        instance.tick()
        assert instance.children['reader'].pid != old.pid
        assert len(instance.children) == 3
    finally:
        pids = [p.pid for p in instance.children.values()]
        instance.stop()
    assert not instance.path.exists()
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_stale_or_missing_child_health_is_not_healthy(tmp_path, monkeypatch):
    file = tmp_path/'status.json'
    monkeypatch.setattr(supervisor, 'process_identity', lambda pid: str(pid))
    children = {name: {'pid': n, 'identity': str(n)}
                for n, name in enumerate(('collector', 'reader', 'worker'), 1)}
    file.write_text(json.dumps({'checked': 10, 'children': children}))
    assert supervisor.health(file, now=11)['healthy']
    assert not supervisor.health(file, now=26)['healthy']
    children.pop('reader')
    file.write_text(json.dumps({'checked': 10, 'children': children}))
    assert not supervisor.health(file, now=11)['healthy']


def test_child_specs_never_inherit_provider_secrets(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-only')
    monkeypatch.setattr(supervisor.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=1000, pw_gid=1000))
    specs = supervisor.specs()
    assert specs['reader']['uid'] == 1001
    assert specs['worker']['uid'] == specs['collector']['uid'] == 1000
    for spec in specs.values():
        assert 'OPENAI_API_KEY' not in spec['env']
        assert '--analyze' not in spec['command']
    assert specs['reader']['env']['PYTHONPATH'] == '/release/src'


@pytest.fixture
def rollout(tmp_path, monkeypatch):
    base, release = tmp_path/'host', tmp_path/'new'
    base.mkdir(); release.mkdir()
    (release/'compose.single.yaml').write_text('one')
    (base/'deployed.json').write_text('{"old":true}')
    monkeypatch.setattr(deployment.shutil, 'disk_usage', lambda _: SimpleNamespace(free=10*1024**3))
    plan = planner.make_plan(release, {})
    previous = {'release': str(tmp_path/'old'), 'hermes_image': 'old-image'}
    return base, release, plan, previous


def test_cutover_stops_writers_then_backs_up_before_new_start(rollout, monkeypatch):
    base, release, plan, previous = rollout
    calls = []
    monkeypatch.setattr(deployment, 'supervised', lambda *a: None)
    deployment.deploy_single(release, previous, plan, base=base,
        run=lambda args, **kw: calls.append([str(a) for a in args]), idle=lambda: True,
        ready=lambda *a, **kw: None)
    stop = next(i for i, c in enumerate(calls) if c[:2] == ['docker', 'stop'])
    backup = next(i for i, c in enumerate(calls) if 'backup' in c)
    up = next(i for i, c in enumerate(calls) if 'up' in c)
    assert stop < backup < up
    assert set(deployment.LEGACY).issubset(calls[stop])
    assert not any('rm' in c or '--volumes' in c for c in calls)
    assert json.loads((base/'deployed.json').read_text())['topology'] == 'single'


def test_failed_readiness_rolls_back_without_advancing_metadata(rollout, monkeypatch):
    base, release, plan, previous = rollout
    calls = []
    monkeypatch.setattr(deployment, 'rollback', lambda *a: calls.append('rollback'))
    def ready(*args, **kwargs):
        if 'rollback' not in calls:
            raise RuntimeError('not_ready')
    with pytest.raises(RuntimeError, match='not_ready'):
        deployment.deploy_single(release, previous, plan, base=base,
            run=lambda *a, **kw: None, idle=lambda: True, ready=ready)
    assert calls == ['rollback']
    assert json.loads((base/'deployed.json').read_text()) == {'old': True}


def test_busy_cutover_never_stops_anything(rollout):
    base, release, plan, previous = rollout
    deployment.deploy_single(release, previous, plan, base=base,
        run=lambda *a, **kw: pytest.fail('runtime changed'), idle=lambda: False,
        ready=lambda *a, **kw: pytest.fail('runtime changed'))
    assert json.loads((base/'deployed.json').read_text()) == {'old': True}


def test_single_backup_uses_current_container(tmp_path, monkeypatch, capsys):
    maintenance = load('services/gujeuk_pm/maintenance.py')
    (tmp_path/'deployed.json').write_text('{"topology":"single"}')
    monkeypatch.setattr(maintenance, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['maintenance', 'backup'])
    calls = []
    monkeypatch.setattr(maintenance, 'run', lambda args, **kw:
        calls.append(args) or SimpleNamespace(returncode=0, stdout='{"verified_databases":2}'))
    maintenance.main()
    assert calls[0][:3] == ['docker', 'exec', '--user']
    assert 'gujeuk-pm-hermes' in calls[0]
    assert '/opt/pm/bin/python' in calls[0]
    assert 'pm-worker' not in calls[0]


@pytest.mark.parametrize('build', [False, True])
def test_space_guard_distinguishes_source_rollout_and_image_build(rollout, monkeypatch, build):
    base, release, plan, previous = rollout
    monkeypatch.setattr(deployment.shutil, 'disk_usage', lambda _: SimpleNamespace(free=2*1024**3))
    monkeypatch.setattr(deployment, 'supervised', lambda *a: None)
    plan['builds'] = ['hermes'] if build else []
    def apply():
        deployment.deploy_single(release, previous, plan, base=base,
            run=lambda *a, **kw: None, idle=lambda: True, ready=lambda *a, **kw: None)
    if build:
        with pytest.raises(ValueError, match='low_disk'):
            apply()
        assert json.loads((base/'deployed.json').read_text()) == {'old': True}
    else:
        apply()
        assert json.loads((base/'deployed.json').read_text())['topology'] == 'single'
