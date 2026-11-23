"""Run via docker exec -i root Python, stdin from this checked-in file. Metadata only."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, '/opt/burserker')
from supervisor import health


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--restart-reader', action='store_true')
    args = parser.parse_args()
    first = health()
    assert first['healthy'], 'children_not_ready'
    env = {'HOME': '/tmp/pm-reader', 'PATH': '/opt/pm/bin:/usr/bin:/bin',
           'PLAYWRIGHT_BROWSERS_PATH': '/opt/browsers', 'PYTHONPATH': '/release/src'}
    browser = (
        "import os;from pathlib import Path;from playwright.sync_api import sync_playwright;"
        "paths=['/opt/data/.env','/opt/data/config.yaml','/opt/data/pm-workspace/workspace.db',"
        "'/run/secrets/discord_token'];assert not any(os.access(p,os.R_OK) for p in paths);"
        "assert os.access('/requests',os.R_OK|os.W_OK);"
        "from oijwef098234.gujeuk_ai.notion_reader import read_page;"
        "p=sync_playwright().start();b=p.chromium.launch(headless=True);"
        "page=b.new_page();page.set_content('<h1>reader health</h1>');"
        "assert page.locator('h1').inner_text()=='reader health';b.close();p.stop()"
    )
    subprocess.run(['/opt/pm/bin/python', '-c', browser], check=True, env=env,
                   user=1001, group=1001, extra_groups=[1002], timeout=40)
    restarted = False
    if args.restart_reader:
        deadline = time.monotonic() + 120
        while list(Path('/requests').glob('*.request.json')):
            if time.monotonic() >= deadline:
                raise RuntimeError('reader_busy_restart_test_deferred')
            time.sleep(0.5)
        old = health()['children']['reader']['pid']
        os.kill(old, signal.SIGTERM)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = health()
            if current['healthy'] and current['children']['reader']['pid'] != old:
                restarted = True
                break
            time.sleep(0.25)
        assert restarted, 'reader_not_restarted'
    gateway = json.loads(Path('/opt/data/gateway_state.json').read_text())
    assert gateway['gateway_state'] == 'running'
    assert gateway['platforms']['discord']['state'] == 'connected'
    print(json.dumps({'children_healthy': health()['healthy'], 'isolated_browser_render': True,
                      'reader_protected_paths_denied': True, 'reader_restarted': restarted,
                      'discord_connected': True}))


if __name__ == '__main__':
    main()
