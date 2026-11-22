"""Content-based agent rollout planning; no Docker, network, secrets or deletions."""
from pathlib import Path
import hashlib

SERVICES = ('hermes', 'collector', 'pm-worker', 'notion-reader')
HERMES_IMAGE_INPUTS = (
    'services/gujeuk_pm/Dockerfile.hermes', 'services/gujeuk_pm/concurrency.py',
    'services/gujeuk_pm/hermes_patches.py', 'services/gujeuk_pm/discord_mentions.py',
)
WORKER_IMAGE_INPUTS = ('services/gujeuk_pm/Dockerfile', 'pyproject.toml', 'uv.lock')
# Conservative dependency groups: shared application code restarts its consumers,
# not the independent collector. The browser has a deliberately narrow surface.
SERVICE_INPUTS = {
    'hermes': ('src', 'services/gujeuk_pm/plugin', 'services/gujeuk_pm/agent-skill',
               'scripts/gateway_policy.py', 'deploy/imported/compose.yaml', 'compose.agent.yaml'),
    'collector': ('services/gujeuk_pm/collector.py', 'deploy/imported/compose.yaml',
                  'deploy/imported/compose.pm.yaml', 'compose.agent.yaml'),
    'pm-worker': ('src', 'services/gujeuk_pm/collector.py', 'deploy/imported/compose.yaml',
                  'deploy/imported/compose.pm.yaml', 'compose.agent.yaml'),
    'notion-reader': ('src/oijwef098234/__init__.py', 'src/oijwef098234/gujeuk_ai/__init__.py',
                      'src/oijwef098234/gujeuk_ai/notion_reader.py',
                      'src/oijwef098234/gujeuk_ai/sources.py', 'deploy/compose.notion.yaml'),
}


def fingerprint(root, inputs):
    """Hash relative names and bytes, never mtimes, caches or directory order."""
    root = Path(root)
    files = set()
    for name in inputs:
        path = root / name
        if path.is_symlink():
            raise ValueError('symlink_runtime_input')
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            for child in path.rglob('*'):
                if '__pycache__' in child.parts or child.suffix in {'.pyc', '.pyo'}:
                    continue
                if child.is_symlink():
                    raise ValueError('symlink_runtime_input')
                if child.is_file():
                    files.add(child)
    digest = hashlib.sha256()
    for path in sorted(files):
        name = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, 'big') + name)
        digest.update(len(data).to_bytes(8, 'big') + data)
    return digest.hexdigest()


def make_plan(release, previous, *, force_worker=False):
    """Keep immutable mount paths/images for unchanged services.

    Old deployed.json records are migrated by comparing their actual release
    files. A missing old release causes a conservative rollout, never a skip.
    """
    release = Path(release)
    old_root = Path(previous.get('release', ''))
    known_old = bool(previous.get('release')) and old_root.is_dir()
    previous_images = previous.get('images', {})
    images, builds = {}, []
    for name, inputs in [('hermes', HERMES_IMAGE_INPUTS), ('worker', WORKER_IMAGE_INPUTS)]:
        current = fingerprint(release, inputs)
        old = previous_images.get(name, {})
        old_digest = old.get('fingerprint') or (fingerprint(old_root, inputs) if known_old else None)
        old_image = old.get('image') or previous.get(name + '_image')
        if name == 'worker' and known_old and not old_image:
            old_image = 'gujeuk-pm-worker:local'  # Existing verified legacy dependency image.
        build = not old_image or old_digest != current or (name == 'worker' and force_worker)
        image = 'gujeuk-pm-' + name + ':' + release.name if build else old_image
        images[name] = {'image': image, 'fingerprint': current}
        if build:
            builds.append(name)
    services, targets = {}, []
    for name in SERVICES:
        image = images['hermes' if name == 'hermes' else 'worker']['image']
        current = fingerprint(release, SERVICE_INPUTS[name])
        old = previous.get('services', {}).get(name, {})
        old_code = Path(old.get('release', old_root))
        available = (bool(old.get('release')) or known_old) and old_code.is_dir()
        old_digest = old.get('fingerprint') or (fingerprint(old_code, SERVICE_INPUTS[name]) if available else None)
        old_image = old.get('image') or (previous.get('hermes_image') if name == 'hermes'
                                       else previous.get('worker_image', 'gujeuk-pm-worker:local'))
        changed = (not available or old_digest != current or old_image != image
                   or ('hermes' if name == 'hermes' else 'worker') in builds)
        services[name] = {'release': str(release if changed else old_code),
                          'fingerprint': current, 'image': image}
        if changed:
            targets.append(name)
    return {'images': images, 'services': services, 'builds': builds, 'targets': targets}
