import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
import pytest


@pytest.fixture
def deploy(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('pull_deploy_test',Path(__file__).parents[1]/'scripts/pull_deploy.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'BASE',tmp_path)
    monkeypatch.setattr(module.time,'time',lambda:1000)
    return module


def test_quota_persisted_across_ticks_and_revisions(deploy,monkeypatch):
    calls=[]
    def limited(*args,**kwargs):
        calls.append(1)
        raise HTTPError('https://api.github.com',403,'rate limit',
            {'X-RateLimit-Remaining':'0','X-RateLimit-Reset':'2000'},None)
    monkeypatch.setattr(deploy,'urlopen',limited)
    assert not deploy.verified_ci('a'*40)
    assert not deploy.verified_ci('b'*40)
    assert len(calls)==1
    state=json.loads((deploy.BASE/'deploy-ci.json').read_text())
    assert state['next_check']==2015
    assert (deploy.BASE/'deploy-ci.json').stat().st_mode&0o777==0o600
    monkeypatch.setattr(deploy.time,'time',lambda:2016)
    assert not deploy.verified_ci('b'*40)
    assert len(calls)==2


@pytest.mark.parametrize('code,headers,reason',[
    (403,{},'ci_http_403'),(401,{},'ci_http_401'),
    (429,{'Retry-After':'600'},'ci_rate_limit_backoff')])
def test_auth_and_quota_are_distinct(deploy,monkeypatch,code,headers,reason):
    def failed(*args,**kwargs):raise HTTPError('https://api.github.com',code,'hidden',headers,None)
    monkeypatch.setattr(deploy,'urlopen',failed)
    assert not deploy.verified_ci('a'*40)
    assert json.loads((deploy.BASE/'deploy-ci.json').read_text())['reason']==reason


def test_exact_ci_receipt_never_authorizes_other_revision(deploy,monkeypatch):
    calls=[]
    def response(*args,**kwargs):
        calls.append(1)
        return io.StringIO(json.dumps({'workflow_runs':[{
            'head_sha':'a'*40,'head_branch':'main','event':'push','status':'completed','conclusion':'success','id':42}]}))
    monkeypatch.setattr(deploy,'urlopen',response)
    assert deploy.verified_ci('a'*40)
    assert deploy.verified_ci('a'*40)
    assert not deploy.verified_ci('b'*40)
    assert len(calls)==1


def test_pending_newer_run_does_not_fall_back_to_old_success(deploy,monkeypatch):
    common={'head_sha':'a'*40,'head_branch':'main','event':'push'}
    monkeypatch.setattr(deploy,'urlopen',lambda *a,**kw:io.StringIO(json.dumps({'workflow_runs':[
        common|{'status':'in_progress','conclusion':None,'id':2},
        common|{'status':'completed','conclusion':'success','id':1}]})))
    assert not deploy.verified_ci('a'*40)


def test_network_backoff_and_symlink_rejection(deploy,monkeypatch):
    def failed(*a,**kw):raise URLError('do not print arbitrary errors')
    monkeypatch.setattr(deploy,'urlopen',failed)
    assert not deploy.verified_ci('a'*40)
    assert json.loads((deploy.BASE/'deploy-ci.json').read_text())['reason']=='ci_network_backoff'
    (deploy.BASE/'deploy-ci.json').unlink()
    (deploy.BASE/'deploy-ci.json').symlink_to(deploy.BASE/'other')
    with pytest.raises(ValueError,match='unsafe_ci_state'):deploy.verified_ci('a'*40)


@pytest.mark.parametrize('headers',[
    {'X-RateLimit-Reset':'nan'}, {'X-RateLimit-Reset':'invalid'},
    {'Retry-After':'Sun, 22 Nov 2026 12:00:00 GMT'}, {'Retry-After':'inf'},
    {'X-RateLimit-Reset':'999999999999'}])
def test_retry_headers_are_bounded(deploy,headers):
    assert 1060<=deploy.retry_time(headers,1000)<=87400
