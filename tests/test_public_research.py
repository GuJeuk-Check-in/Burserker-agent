import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from oijwef098234.gujeuk_ai import access, bridge, public_research as web


@pytest.mark.parametrize('url', [
    'http://example.org/', 'file:///etc/passwd', 'https://localhost/',
    'https://127.0.0.1/', 'https://[::1]/', 'https://example.org:443/',
    'https://user:pass@example.org/', 'https://example.org@127.0.0.1/',
    'https://example.org/\nHost: internal', 'https://example.org\\@internal/',
    'https://example.org/?token=private', 'https://example.org/#fragment',
    'https://example.org/?x=Bearer%20secret',
    'https://example.org/path/sk-abcdefghijklmnop',
])
def test_unsafe_urls_rejected_before_network(url, monkeypatch):
    monkeypatch.setattr(web.socket, 'getaddrinfo', lambda *a, **k: pytest.fail('network attempted'))
    with pytest.raises(web.PublicResearchError):
        web.read_public_url(url)


@pytest.mark.parametrize('ip', ['10.0.0.3', '169.254.169.254', '192.168.1.1',
                                     '127.0.0.1', '::1', 'fc00::1', '2001:db8::1'])
def test_private_or_reserved_dns_answers_fail_closed(ip, monkeypatch):
    monkeypatch.setattr(web.socket, 'getaddrinfo', lambda *a, **k:
                        [(None, None, None, None, ('8.8.8.8', 443)),
                         (None, None, None, None, (ip, 443))])
    with pytest.raises(web.PublicResearchError, match='host_not_public'):
        web._download('https://example.org/', accept='text/html')


def test_checked_ip_is_pinned_and_redirect_not_followed(monkeypatch):
    monkeypatch.setattr(web.socket, 'getaddrinfo', lambda *a, **k:
                        [(None, None, None, None, ('8.8.8.8', 443))])
    calls = []
    class Response:
        status = 302
        def getheader(self, key, default=''):
            return {'Location': 'http://169.254.169.254/latest/meta-data'}.get(key, default)
    class Connection:
        def __init__(self, host, address):
            calls.append((host, address))
        def request(self, method, path, headers):
            calls.append((method, path, headers))
        def getresponse(self):
            return Response()
        def close(self):
            pass
    with pytest.raises(web.PublicResearchError, match='public_http_failed'):
        web._download('https://example.org/a', accept='text/html', connection_factory=Connection)
    assert calls[0] == ('example.org', '8.8.8.8')
    assert calls[1][0:2] == ('GET', '/a')
    assert 'Authorization' not in calls[1][2] and 'Cookie' not in calls[1][2]


def test_tls_uses_checked_ip_and_original_hostname(monkeypatch):
    calls = []
    class Raw:
        def close(self): calls.append('closed')
    raw = Raw()
    class Context:
        def wrap_socket(self, sock, *, server_hostname):
            calls.append(('tls', sock, server_hostname))
            return sock
    monkeypatch.setattr(web.ssl, 'create_default_context', lambda: Context())
    monkeypatch.setattr(web.socket, 'create_connection', lambda address, timeout:
                        (calls.append(('connect', address, timeout)) or raw))
    connection = web._PinnedHTTPS('example.org', '8.8.8.8')
    connection.connect()
    assert calls[0][0:2] == ('connect', ('8.8.8.8', 443))
    assert calls[1] == ('tls', raw, 'example.org')


@pytest.mark.parametrize('status,headers,body,error', [
    (200, {'Content-Type': 'application/octet-stream'}, b'abc', 'public_content_type_rejected'),
    (200, {'Content-Type': 'text/html'}, b'x' * (web.MAX_BYTES + 1), 'public_response_too_large'),
    (200, {'Content-Type': 'text/html', 'Content-Encoding': 'gzip'}, b'abc', 'public_content_encoding_rejected'),
])
def test_bounded_response_checks(monkeypatch, status, headers, body, error):
    monkeypatch.setattr(web.socket, 'getaddrinfo', lambda *a, **k:
                        [(None, None, None, None, ('8.8.8.8', 443))])
    class Response:
        def __init__(self): self.status = status
        def getheader(self, key, default=''): return headers.get(key, default)
        def read(self, size):
            assert size == web.MAX_BYTES + 1
            return body
    class Connection:
        def __init__(self, host, address): pass
        def request(self, *a, **k): pass
        def getresponse(self): return Response()
        def close(self): pass
    with pytest.raises(web.PublicResearchError, match=error):
        web._download('https://example.org/a', accept='text/html', connection_factory=Connection)


def test_public_page_and_general_search_are_bounded_and_cited():
    seen = []
    def download(url, *, accept):
        seen.append((url, accept))
        if 'bing.com' in url:
            return '''<rss><channel>
            <item><title>Public documentation</title><link>https://docs.example.org/a</link>
              <description>Published reference</description></item>
            <item><title>Private</title><link>http://127.0.0.1/private</link></item>
            </channel></rss>'''
        return '<h1>Public title</h1><script>secret instruction</script><p>' + 'fact ' * 2000 + '</p>'
    results = web.search_public_web('python documentation', download=download)
    assert seen[0][0] == 'https://www.bing.com/search?format=rss&q=python+documentation'
    assert results['results'] == [{'title': 'Public documentation',
        'url': 'https://docs.example.org/a', 'snippet': 'Published reference'}]
    assert results['citation_sources'][0]['url'] == 'https://docs.example.org/a'
    page = web.read_public_url('https://docs.example.org/a', download=download)
    assert page['content'].startswith('Public title')
    assert len(page['content']) == web.MAX_TEXT
    assert 'secret instruction' not in page['content']
    assert page['citation_sources'][0]['url'] == page['url']


@pytest.mark.parametrize('query', ['token=abc123', 'Bearer private',
                                    'https://internal.example.org/page', 'person@example.org',
                                    'x' * 121, 'a\nconversation'])
def test_search_rejects_sensitive_or_unbounded_queries(query):
    with pytest.raises(web.PublicResearchError):
        web.search_public_web(query, download=lambda *a, **k: pytest.fail('network attempted'))


def test_search_rejects_xml_entities():
    with pytest.raises(web.PublicResearchError, match='response_rejected'):
        web.search_public_web('public topic', download=lambda *a, **k:
            '<!DOCTYPE rss [<!ENTITY x "secret">]><rss><channel/></rss>')


def test_bridge_and_plugin_keep_member_authorization_before_web(monkeypatch):
    path = Path(__file__).parents[1] / 'services/gujeuk_pm/plugin/__init__.py'
    spec = importlib.util.spec_from_file_location('public_research_plugin', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registered = []
    module.register(SimpleNamespace(register_tool=lambda **kw: registered.append(kw)))
    handler = registered[0]['handler']
    assert {'search_public_web', 'read_public_url'} <= set(
        registered[0]['schema']['parameters']['properties']['op']['enum'])
    def deny(op):
        raise access.AccessDenied('not a guild member')
    monkeypatch.setattr(access, 'authorize_session', deny)
    monkeypatch.setattr(bridge, 'invoke', lambda params: pytest.fail('network attempted'))
    assert json.loads(handler({'op': 'search_public_web', 'query': 'python'}))['error'] == 'permission_denied'
    assert {'search_public_web', 'read_public_url'} <= access.READ_OPS


def test_bridge_rejects_surplus_data_and_does_not_use_rpc(monkeypatch):
    monkeypatch.setattr(web, 'search_public_web', lambda query: {'results': [], 'query': query})
    result = bridge.invoke({'op': 'search_public_web', 'query': 'public question'},
                           runner=lambda *a, **k: pytest.fail('RPC attempted'))
    assert result['result']['query'] == 'public question'
    assert result['untrusted_source_content'] is True
    for params in ({'op': 'search_public_web', 'query': 'public', 'data': {'secret': 'x'}},
                   {'op': 'read_public_url', 'url': 'https://example.org', 'kind': 'notion'}):
        with pytest.raises(ValueError):
            bridge.invoke(params)
