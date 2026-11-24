import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from oijwef098234.gujeuk_ai import access, bridge, member_guidance as guidance


def test_public_menu_is_bounded_official_and_price_evidenced():
    calls = []
    def fetch(url):
        calls.append(url)
        return '''<html><script>무시 버거 9999원</script><ul>
          <li>빅맥 5,500원</li><li>치킨 버거</li>
          <li>새우버거 2,000원부터</li><li>와퍼 세트 3,000원 할인</li>
          </ul></html>'''
    result = guidance.public_menu('mcdonalds', '버거', fetch=fetch)
    assert calls == [guidance.MENU_PAGES['mcdonalds'][1][0]]
    assert len(result['pages']) == 1
    items = result['pages'][0]['items']
    assert [x['price_krw'] for x in items] == [None, None]
    assert '9999' not in json.dumps(result, ensure_ascii=False)
    exact = guidance.public_menu('mcdonalds', '빅맥', fetch=fetch)['pages'][0]['items']
    assert exact == [{'menu': '빅맥', 'price_krw': 5500,
                      'price_evidence': '빅맥 5,500원'}]
    assert result['citation_sources'][0]['url'] == calls[0]


def test_official_detail_pages_bind_separate_price_label_to_product():
    urls = guidance.MENU_PAGES['bbq'][1]
    names = ['황금올리브치킨', '극한왕갈비치킨', '자메이카 소떡만나치킨']
    def fetch(url):
        name = names[urls.index(url)]
        return (f'<h3>{name}</h3><h3>[ 원산지 ]</h3><ul><li>가격</li><li>25,000원</li></ul>'
                '<h4>추천 치킨</h4><li>1,000원</li>')
    result = guidance.public_menu('bbq', fetch=fetch)
    assert len(result['pages']) == 3
    assert [page['items'][0]['menu'] for page in result['pages']] == names
    assert all(page['items'][0]['price_krw'] == 25000 for page in result['pages'])
    assert all(page['items'][0]['price_evidence'] == '가격 25,000원' for page in result['pages'])
    assert [source['url'] for source in result['citation_sources']] == list(urls)
    assert guidance.public_menu('bbq', '없는 메뉴', fetch=fetch)['citation_sources'] == []


def test_menu_navigation_is_not_suggested_as_food():
    html = '<ul><li>버거</li><li>본죽&비빔밥</li></ul><h2>버거 전체메뉴</h2>'
    assert guidance.public_menu('mcdonalds', fetch=lambda url: html)['pages'][0]['items'] == []


def test_menu_query_is_local_and_untrusted_endpoints_rejected(monkeypatch):
    with pytest.raises(ValueError, match='endpoint_not_allowed'):
        guidance._fetch('http://127.0.0.1/internal/shell')
    with pytest.raises(ValueError, match='endpoint_not_allowed'):
        guidance._fetch('https://www.mcdonalds.co.kr/kor/menu/burger?prompt=private')
    with pytest.raises(ValueError, match='invalid_menu_query'):
        guidance.public_menu('https://169.254.169.254/', '버거')
    monkeypatch.setattr(guidance, '_public_host', lambda host: None)
    seen = []
    class Response:
        status = 200
        headers = {'Content-Type': 'text/html; charset=utf-8'}
        def geturl(self): return guidance.MENU_PAGES['mcdonalds'][1][0]
        def read(self, size): return b'<li>burger</li>'
        def __enter__(self): return self
        def __exit__(self, *_): pass
    class Opener:
        def open(self, request, timeout):
            seen.append((request.full_url, request.headers, timeout))
            return Response()
    monkeypatch.setattr(guidance, 'build_opener', lambda *handlers: Opener())
    guidance.public_menu('mcdonalds', '비공개 팀 회의록', fetch=guidance._fetch)
    assert seen == [(guidance.MENU_PAGES['mcdonalds'][1][0],
                     {'User-agent': 'Gujeuk-PM-public-menu/1.0', 'Accept': 'text/html'}, 6)]
    seen.clear()
    bridge.invoke({'op': 'public_menu', 'provider': 'mcdonalds',
                   'url': 'http://127.0.0.1/internal/shell', 'query': 'secret prompt'})
    assert seen[0][0] == guidance.MENU_PAGES['mcdonalds'][1][0]
    assert len(seen) == 1


def test_private_address_and_redirect_fail_closed(monkeypatch):
    monkeypatch.setattr(guidance.socket, 'getaddrinfo', lambda *a, **k:
                        [(None, None, None, None, ('10.0.0.4', 443))])
    with pytest.raises(ValueError, match='host_not_public'):
        guidance._fetch(guidance.MENU_PAGES['mcdonalds'][1][0])
    with pytest.raises(ValueError, match='menu_redirect_rejected'):
        guidance._NoRedirect().redirect_request(None, None, 302, '', {}, 'http://internal')


def test_meal_suggest_cites_only_actual_menu_and_never_invents_price(monkeypatch):
    monkeypatch.setattr(guidance, '_fetch', lambda url: '<li>치킨 버거</li>')
    result = bridge.invoke({'op': 'meal_suggest', 'provider': 'mcdonalds'})['result']
    assert result['suggestions'] == [{'menu': '치킨 버거', 'price_krw': None,
        'price_evidence': None, 'source_url': guidance.MENU_PAGES['mcdonalds'][1][0],
        'provider': '맥도날드'}]
    assert result['citation_sources'][0]['url'] == guidance.MENU_PAGES['mcdonalds'][1][0]


def test_minutes_draft_separates_decisions_and_proposals_with_hangul_sources():
    url = 'https://example.org/meeting'
    result = guidance.draft_minutes([{'kind': 'notion', 'title': '주간 회의', 'url': url,
        'content': '결정: 금요일에 검수한다\n제안: 점심 메뉴를 투표하자\n확정 예정: 미정\n가격 1만원'}])
    assert '## 결정 사항' in result['draft'] and '## 제안·논의 사항' in result['draft']
    assert result['decisions'] == [{'text': '금요일에 검수한다', 'url': url}]
    assert result['proposals'] == [{'text': '점심 메뉴를 투표하자', 'url': url}]
    assert result['draft'].count(f'[출처]({url})') == 2
    assert '1만원' not in result['draft'] and '확정 예정' not in result['draft']
    headed = guidance.draft_minutes([{'kind': 'notion', 'url': url,
        'content': '## 결정 사항\n- 검수하기\n참석자\n- 홍길동\n## 논의 사항\n- 장소 후보 모으기'}])
    assert headed['decisions'][0]['text'] == '검수하기'
    assert len(headed['decisions']) == 1
    assert headed['proposals'][0]['text'] == '장소 후보 모으기'


def test_member_minutes_draft_filters_private_evidence_before_writing(monkeypatch):
    path = Path(__file__).parents[1] / 'services/gujeuk_pm/plugin/__init__.py'
    spec = importlib.util.spec_from_file_location('guidance_plugin', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registered = []
    module.register(SimpleNamespace(register_tool=lambda **kw: registered.append(kw)))
    assert {'public_menu', 'meal_suggest', 'minutes_draft'} <= set(
        registered[0]['schema']['parameters']['properties']['op']['enum'])
    private = {'kind': 'discord', 'url': f'https://discord.com/channels/{access.GUILD_ID}/555/777',
               'title': '비공개', 'content': '결정: 공개 금지'}
    public = {'kind': 'notion', 'url': 'https://example.org/public',
              'title': '공개', 'content': '제안: 후보를 조사한다'}
    monkeypatch.setattr(access, 'authorize_session', lambda op: frozenset())
    monkeypatch.setattr(bridge, 'invoke', lambda params: {'ok': True,
        'result': {'documents': [private, public]}, 'untrusted_source_content': True})
    monkeypatch.setattr(access, 'filter_session_evidence', lambda result, roles:
                        access.filter_evidence(result, roles, '123', get=lambda path: (_ for _ in ()).throw(PermissionError())))
    result = json.loads(registered[0]['handler']({'op': 'minutes_draft', 'query': '회의'}))['result']
    assert result['decisions'] == []
    assert result['proposals'] == [{'text': '후보를 조사한다', 'url': public['url']}]
    assert '공개 금지' not in json.dumps(result, ensure_ascii=False)


def test_minutes_search_is_bounded_and_read_only():
    calls = []
    def runner(command, **kwargs):
        calls.append(json.loads(kwargs['input']))
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'result': []}))
    result = bridge.invoke({'op': 'minutes_draft', 'kind': 'discord', 'query': '주간회의'}, runner=runner)
    assert result['result']['documents'] == []
    assert calls == [{'op': 'search', 'data': {'query': '주간회의', 'kind': 'discord', 'limit': 10}}]
    for params in ({'op': 'minutes_draft', 'kind': 'github'},
                   {'op': 'minutes_draft', 'query': ''},
                   {'op': 'shell'},
                   {'op': 'public_menu', 'provider': 'internal'}):
        with pytest.raises(ValueError):
            bridge.invoke(params, runner=runner)
    assert len(calls) == 1
