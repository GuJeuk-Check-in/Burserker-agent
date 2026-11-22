"""Credential enrollment diagnostics: offline fixtures, never real tokens."""
import importlib.util
from email.message import Message
from pathlib import Path
import ssl
from urllib.error import HTTPError, URLError

import pytest


@pytest.fixture
def enrollment(monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'enroll_figma.py'
    spec = importlib.util.spec_from_file_location('enroll_figma', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.getpass, 'getpass', lambda prompt: 'fixture-token')
    return module


def fail_open(monkeypatch, module, error):
    class Opener:
        def open(self, *args, **kwargs):
            raise error
    monkeypatch.setattr(module, 'build_opener', lambda *args: Opener())


def test_certificate_failure_is_not_reported_as_invalid_token(enrollment, monkeypatch, capsys):
    fail_open(monkeypatch, enrollment,
              URLError(ssl.SSLCertVerificationError(1, 'fixture-token')))
    assert enrollment.main() == 1
    output = capsys.readouterr().out
    assert '토큰 입력 문제가 아니라' in output
    assert 'uv run --frozen --extra pm-server' in output
    assert 'fixture-token' not in output


def test_network_failure_does_not_print_arbitrary_error(enrollment, monkeypatch, capsys):
    fail_open(monkeypatch, enrollment, URLError('fixture-token'))
    assert enrollment.main() == 1
    output = capsys.readouterr().out
    assert '네트워크·프록시·DNS' in output
    assert 'fixture-token' not in output


def test_http_status_remains_distinct(enrollment, monkeypatch, capsys):
    fail_open(monkeypatch, enrollment, HTTPError('https://api.figma.com', 403, 'fixture-token', Message(), None))
    assert enrollment.main() == 1
    output = capsys.readouterr().out
    assert 'HTTP 403' in output
    assert 'fixture-token' not in output


def test_oversized_response_fails_before_json_parse_or_ssh(enrollment, monkeypatch, capsys):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            assert limit == 65
            return b'x' * limit
    class Opener:
        def open(self, *args, **kwargs):
            return Response()
    monkeypatch.setattr(enrollment, 'MAX_VALIDATION_RESPONSE', 64)
    monkeypatch.setattr(enrollment, 'build_opener', lambda *args: Opener())
    assert enrollment.main() == 1
    assert '응답 형식이나 크기' in capsys.readouterr().out
