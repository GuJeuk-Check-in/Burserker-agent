import runpy
from pathlib import Path

select = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/host_image_retention.py'))['candidates']


def test_only_observed_retired_with_matching_tags():
    name = 'gujeuk-pm-hermes'
    tag = name + ':' + 'a'*40
    rows = {name:['cid','new','running','2020-01-01T00:00:00Z',0,'healthy',[]], 'stopped':['other','used','exited','',0,'none',[]]}
    images = {i:{tag} for i in ['new','used','old','staged','shared']}
    images['shared'].add('unknown:prod')
    history = {name:['used','old','shared','new']}
    assert select(history, rows, images, 1900000000) == {'old'}
    assert not select(history, rows, images, 1577836801)
    rows[name][5] = 'unhealthy'
    assert not select(history, rows, images, 1900000000)
