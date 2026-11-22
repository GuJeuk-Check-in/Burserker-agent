"""Offline rollout regressions: never contact Docker, AWS or production."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import itertools
import json
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = spec_from_file_location('test_' + name, ROOT / 'scripts' / (name + '.py'))
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


planner = load('deploy_plan')


def put(root, name, content='baseline'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def releases(tmp_path):
    old, new = tmp_path / ('a' * 40), tmp_path / ('b' * 40)
    paths = set(planner.HERMES_IMAGE_INPUTS + planner.WORKER_IMAGE_INPUTS)
    for inputs in planner.SERVICE_INPUTS.values():
        paths.update(inputs)
    paths -= {'src', 'services/gujeuk_pm/plugin', 'services/gujeuk_pm/agent-skill'}
    paths.update({'src/oijwef098234/gujeuk_ai/wiki.py',
                  'services/gujeuk_pm/plugin/__init__.py', 'services/gujeuk_pm/agent-skill/SKILL.md'})
    for path in paths:
        put(old, path, path)
    shutil.copytree(old, new)
    previous = {'release': str(old), 'revision': old.name,
                'hermes_image': 'gujeuk-pm-hermes:' + old.name}
    return old, new, previous


def test_legacy_release_identical_code_does_not_restart(releases):
    old, new, previous = releases
    plan = planner.make_plan(new, previous)
    assert plan['builds'] == plan['targets'] == []
    assert all(service['release'] == str(old) for service in plan['services'].values())
    assert plan['images']['worker']['image'] == 'gujeuk-pm-worker:local'


@pytest.mark.parametrize('path,targets,builds', [
    ('docs/operations.md', [], []),
    ('scripts/enroll_figma.py', [], []),
    ('tests/test_example.py', [], []),
    ('src/oijwef098234/gujeuk_ai/wiki.py', ['hermes', 'pm-worker'], []),
    ('src/oijwef098234/gujeuk_ai/sources.py', ['hermes', 'pm-worker', 'notion-reader'], []),
    ('services/gujeuk_pm/collector.py', ['collector', 'pm-worker'], []),
    ('services/gujeuk_pm/concurrency.py', ['hermes'], ['hermes']),
    ('pyproject.toml', ['collector', 'pm-worker', 'notion-reader'], ['worker']),
    ('uv.lock', ['collector', 'pm-worker', 'notion-reader'], ['worker']),
    ('deploy/compose.notion.yaml', ['notion-reader'], []),
    ('scripts/gateway_policy.py', ['hermes'], []),
])
def test_only_consumers_of_changed_inputs_roll_out(releases, path, targets, builds):
    old, new, previous = releases
    put(new, path, 'changed')
    plan = planner.make_plan(new, previous)
    assert plan['targets'] == targets
    assert plan['builds'] == builds
    for name, service in plan['services'].items():
        assert service['release'] == str(new if name in targets else old)


def test_successive_doc_commits_preserve_each_service_actual_mount(releases, tmp_path):
    old, new, previous = releases
    put(new, 'src/oijwef098234/gujeuk_ai/wiki.py', 'changed')
    plan = planner.make_plan(new, previous)
    state = previous | plan | {'release': str(new)}
    third = tmp_path / ('c' * 40)
    shutil.copytree(new, third)
    put(third, 'docs/operations.md', 'documentation only')
    next_plan = planner.make_plan(third, state)
    assert next_plan['targets'] == next_plan['builds'] == []
    assert next_plan['services']['hermes']['release'] == str(new)
    assert next_plan['services']['collector']['release'] == str(old)
    assert next_plan['services']['notion-reader']['release'] == str(old)


def test_missing_old_release_fails_conservatively(releases):
    old, new, previous = releases
    shutil.rmtree(old)  # Synthetic test fixture only.
    plan = planner.make_plan(new, previous)
    assert plan['targets'] == list(planner.SERVICES)
    assert plan['builds'] == ['hermes', 'worker']


def test_explicit_worker_rebuild_rolls_consumers_even_at_same_revision(releases):
    old, new, previous = releases
    plan = planner.make_plan(new, previous, force_worker=True)
    state = previous | plan | {'release': str(new)}
    repeat = planner.make_plan(new, state, force_worker=True)
    assert repeat['builds'] == ['worker']
    assert repeat['targets'] == ['collector', 'pm-worker', 'notion-reader']


def test_bytecode_and_mtimes_are_not_runtime_changes(releases):
    old, new, previous = releases
    put(new, 'src/oijwef098234/__pycache__/wiki.cpython-313.pyc', 'cache')
    (new / 'src/oijwef098234/gujeuk_ai/wiki.py').touch()
    assert planner.make_plan(new, previous)['targets'] == []


def test_symlink_runtime_inputs_are_rejected(releases, tmp_path):
    old, new, previous = releases
    (new / 'src' / 'link').symlink_to(old / 'src', target_is_directory=True)
    with pytest.raises(ValueError, match='symlink_runtime_input'):
        planner.make_plan(new, previous)


@pytest.fixture
def deployment(releases, tmp_path, monkeypatch):
    old, new, previous = releases
    module = load('deploy')
    base = tmp_path / 'production-fixture'
    (base / 'data').mkdir(parents=True)
    (base / 'deployed.json').write_text(json.dumps(previous))
    monkeypatch.setattr(module, 'BASE', base)
    monkeypatch.setattr(module, 'prepare_release', lambda *args: new)
    monkeypatch.setattr(module.os, 'umask', lambda mode: 0)
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda path: SimpleNamespace(free=10 * 1024**3))
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    monkeypatch.setattr(sys, 'argv', ['deploy.py', '--revision', new.name])
    return module, base, previous


def test_documentation_deploy_never_calls_docker_or_backs_up_db(deployment, monkeypatch):
    module, base, previous = deployment
    monkeypatch.setattr(module, 'run', lambda *a, **k: pytest.fail('unexpected runtime command'))
    module.main()
    state = json.loads((base / 'deployed.json').read_text())
    assert state['revision'] == 'b' * 40
    assert state['targets'] == []
    assert state['services']['hermes']['release'] == previous['release']


def test_busy_gateway_defers_before_backup_build_or_state_update(deployment, releases, monkeypatch):
    module, base, previous = deployment
    put(releases[1], 'src/oijwef098234/gujeuk_ai/wiki.py', 'changed')
    monkeypatch.setattr(module, 'gateway_idle', lambda: False)
    monkeypatch.setattr(module, 'run', lambda *a, **k: pytest.fail('unexpected runtime command'))
    module.main()
    assert json.loads((base / 'deployed.json').read_text()) == previous


def test_collector_rollout_never_restarts_gateway_or_reader(deployment, releases, monkeypatch):
    module, base, previous = deployment
    put(releases[1], 'services/gujeuk_pm/collector.py', 'changed')
    calls = []
    monkeypatch.setattr(module, 'run', lambda args, **kwargs: calls.append([str(x) for x in args]))
    monkeypatch.setattr(module, 'gateway_idle', lambda: pytest.fail('gateway is not a target'))
    monkeypatch.setattr(module, 'wait_ready', lambda targets, started: calls.append(['ready', *targets]))
    module.main()
    up = [args for args in calls if 'up' in args]
    assert len(up) == 1
    assert up[0][-6:] == ['up', '-d', '--no-build', '--no-deps', 'collector', 'pm-worker']
    assert not any('--force-recreate' in args or 'build' in args for args in calls)
    state = json.loads((base / 'deployed.json').read_text())
    assert state['services']['hermes']['release'] == previous['release']
    assert state['services']['notion-reader']['release'] == previous['release']


def test_question_arriving_during_build_defers_restart(deployment, releases, monkeypatch):
    module, base, previous = deployment
    put(releases[1], 'services/gujeuk_pm/concurrency.py', 'changed')
    idle = iter([True, False])
    calls = []
    monkeypatch.setattr(module, 'gateway_idle', lambda: next(idle))
    monkeypatch.setattr(module, 'run', lambda args, **kwargs: calls.append([str(x) for x in args]))
    module.main()
    assert any('build' in args for args in calls)
    assert not any('up' in args for args in calls)
    assert json.loads((base / 'deployed.json').read_text()) == previous


def test_readiness_failure_does_not_advance_success_metadata(deployment, releases, monkeypatch):
    module, base, previous = deployment
    put(releases[1], 'services/gujeuk_pm/collector.py', 'changed')
    monkeypatch.setattr(module, 'run', lambda *args, **kwargs: None)
    def fail(*args):
        raise RuntimeError('readiness_failed')
    monkeypatch.setattr(module, 'wait_ready', fail)
    with pytest.raises(RuntimeError, match='readiness_failed'):
        module.main()
    assert json.loads((base / 'deployed.json').read_text()) == previous


@pytest.mark.parametrize('active,wiki,expected', [(0, 'inactive', True), (0, 'failed', True),
    (1, 'inactive', False), (None, 'inactive', False), (False, 'inactive', False),
    (0, 'activating', False), (0, 'active', False), (0, 'deactivating', False)])
def test_idle_guard_preserves_inflight_work(active, wiki, expected, monkeypatch):
    module = load('deploy')
    monkeypatch.setattr(module, 'gateway_status', lambda: {'active': active})
    monkeypatch.setattr(module, 'run', lambda *a, **k: SimpleNamespace(stdout=wiki))
    assert module.gateway_idle() is expected


def test_readiness_requires_fresh_discord_connected_status(monkeypatch):
    module = load('deploy')
    monkeypatch.setattr(module, 'run', lambda *a, **k: SimpleNamespace(stdout='true false false\n'))
    monkeypatch.setattr(module, 'gateway_status', lambda: {'modified': 20, 'gateway': 'running', 'discord': 'connected'})
    module.wait_ready(['hermes'], 10)
    clock = itertools.count()
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: None)
    with pytest.raises(RuntimeError, match='readiness_failed'):
        module.wait_ready(['hermes'], 30, timeout=2)


def test_real_compose_config_keeps_service_specific_mounts(tmp_path, monkeypatch):
    if not shutil.which('docker'):
        pytest.skip('Docker Compose available in CI; no Docker daemon needed')
    module = load('deploy')
    pins = {name: {'release': str(tmp_path / name)} for name in planner.SERVICES}
    args, env = module.compose(ROOT, 'hermes:test', worker_image='worker:test', services=pins)
    result = subprocess.run(args + ['config', '--format', 'json'], env=env, capture_output=True, text=True, check=True)
    services = json.loads(result.stdout)['services']
    for name, destination in [('hermes', '/opt/data/workspace/gujeuk-pm/src'), ('pm-worker', '/release/src')]:
        volume = next(v for v in services[name]['volumes'] if v['target'] == destination)
        assert volume['source'] == str(tmp_path / name / 'src')
        assert volume['read_only'] is True
    reader = ['docker', 'compose', '--project-directory', str(module.BASE), '-f', str(ROOT / 'deploy/compose.notion.yaml')]
    data = json.loads(subprocess.run(reader + ['config', '--format', 'json'], env=env,
                                    capture_output=True, text=True, check=True).stdout)
    volume = next(v for v in data['services']['notion-reader']['volumes'] if v['target'] == '/release/src')
    assert volume['source'] == str(tmp_path / 'notion-reader' / 'src')
