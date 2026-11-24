"""Bounded public menu evidence and source-grounded Korean meeting drafts."""
from __future__ import annotations

from html.parser import HTMLParser
import ipaddress
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .sources import now


# Fixed public pages only. Query text is used locally after download, never sent
# to a provider. Add a provider only after reviewing its public menu endpoint.
MENU_PAGES = {
    'bbq': ('BBQ', (
        'https://mt.bbq.co.kr/menu/menuView.asp?midx=2259',
        'https://mt.bbq.co.kr/menu/menuView.asp?midx=2258',
        'https://mt.bbq.co.kr/menu/menuView.asp?midx=2640',
    )),
    'mcdonalds': ('맥도날드', ('https://www.mcdonalds.co.kr/kor/menu/burger',)),
    'bonif': ('본죽&비빔밥', ('https://www.bonif.co.kr/brand/menu?brdCd=BF102',)),
}
MAX_RESPONSE = 256 * 1024
EXACT_PRICE = re.compile(r'([가-힣A-Za-z0-9®™&() ]{2,50})\s+(\d{1,2}(?:,\d{3})+|\d{4,6})원')
MENU_WORD = re.compile(r'버거|와퍼|세트|치킨|샐러드|너겟|프라이|스낵랩|머핀|빅맥|비빔밥|죽')
HANGUL = re.compile(r'[가-힣]')
NAV_LABELS = frozenset({'버거', '치킨', '본죽', '본죽&비빔밥', '비빔밥', '세트', '메뉴'})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError('menu_redirect_rejected')


class _MenuText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.active = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'template', 'noscript'}:
            self.hidden += 1
        if not self.hidden and tag in {'li', 'tr', 'h2', 'h3', 'h4'}:
            self.active.append([tag, []])

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'template', 'noscript'}:
            self.hidden = max(0, self.hidden - 1)
        if self.active and self.active[-1][0] == tag:
            _, parts = self.active.pop()
            line = re.sub(r'\s+', ' ', ' '.join(parts)).strip()
            if line:
                self.blocks.append((tag, line[:240]))

    def handle_data(self, data):
        if not self.hidden:
            for _, parts in self.active:
                parts.append(data)


def _public_host(host):
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError('menu_dns_failed') from None
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('menu_host_not_public')


def _fetch(url):
    # Both host and path come exclusively from MENU_PAGES. No environment proxy,
    # redirect, arbitrary URL, cookie, Authorization header or private prompt.
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('menu_endpoint_not_allowed')
    if url not in {page for _, pages in MENU_PAGES.values() for page in pages}:
        raise ValueError('menu_endpoint_not_allowed')
    _public_host(parsed.hostname)
    opener = build_opener(ProxyHandler({}), HTTPSHandler(), _NoRedirect())
    request = Request(url, headers={'User-Agent': 'Gujeuk-PM-public-menu/1.0', 'Accept': 'text/html'})
    try:
        with opener.open(request, timeout=6) as response:
            if response.status != 200 or urlsplit(response.geturl()).geturl() != url:
                raise ValueError('menu_response_rejected')
            if 'text/html' not in response.headers.get('Content-Type', '').lower():
                raise ValueError('menu_content_type_rejected')
            body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise ValueError('menu_response_too_large')
            return body.decode('utf-8', errors='replace')
    except (HTTPError, URLError, TimeoutError, OSError):
        raise ValueError('menu_fetch_failed') from None


def _menu_items(html, query, url):
    parser = _MenuText()
    parser.feed(html)
    if url.startswith('https://mt.bbq.co.kr/menu/menuView.asp?'):
        for index, (tag, line) in enumerate(parser.blocks):
            if tag != 'h3' or not MENU_WORD.search(line):
                continue
            if query and query not in line:
                return []
            price = None
            saw_label = False
            for next_tag, next_line in parser.blocks[index + 1:index + 25]:
                if next_tag in {'h3', 'h4'} and MENU_WORD.search(next_line):
                    break
                if next_line == '가격':
                    saw_label = True
                    continue
                inline = re.fullmatch(r'가격\s+(\d{1,2}(?:,\d{3})+|\d{4,6})\s*원', next_line)
                amount = inline or (re.fullmatch(r'(\d{1,2}(?:,\d{3})+|\d{4,6})\s*원', next_line)
                                    if saw_label else None)
                if amount:
                    price = int(amount.group(1).replace(',', ''))
                    break
            return [{'menu': line, 'price_krw': price,
                     'price_evidence': f'가격 {price:,}원' if price is not None else None}]
        return []
    found, seen = [], set()
    for tag, line in parser.blocks:
        if (tag not in {'li', 'tr', 'h3', 'h4'} or line in NAV_LABELS
                or line.endswith(('전체메뉴', '추천메뉴'))
                or not HANGUL.search(line) or not MENU_WORD.search(line)
                or (query and query not in line)):
            continue
        if line in seen or len(line) > 180:
            continue
        seen.add(line)
        # A promotion, range or multi-item block does not establish a single
        # current item price. Require exactly "menu name amount원".
        price = EXACT_PRICE.fullmatch(line) if len(line) <= 100 else None
        if price and re.search(r'할인|부터|최대|쿠폰|정상가', price.group(1)):
            price = None
        found.append({'menu': price.group(1).strip() if price else line[:120],
                      'price_krw': int(price.group(2).replace(',', '')) if price else None,
                      'price_evidence': line if price else None})
        if len(found) == 12:
            break
    return found


def public_menu(provider='all', query='', *, fetch=None):
    if provider not in {'all', *MENU_PAGES} or not isinstance(query, str) or len(query) > 40:
        raise ValueError('invalid_menu_query')
    query = query.strip()
    if fetch is None:
        fetch = _fetch
    selected = MENU_PAGES.items() if provider == 'all' else [(provider, MENU_PAGES[provider])]
    pages = []
    for key, (name, urls) in selected:
        for url in urls:
            try:
                items = _menu_items(fetch(url), query, url)
                pages.append({'provider': key, 'name': name, 'url': url, 'items': items,
                              'status': 'read' if items else 'no_matching_menu'})
            except ValueError:
                pages.append({'provider': key, 'name': name, 'url': url, 'items': [],
                              'status': 'unavailable'})
    return {'observed_at': now(), 'pages': pages,
            'limits': '공식 공개 메뉴 페이지 최대 5곳을 읽어요. 메뉴·가격은 페이지 표기와 방문 시점 기준이며, 가격 미표시는 확인 필요예요. 매장·배달·행사 가격은 다를 수 있어요.',
            'citation_sources': [{'kind': 'public_menu', 'title': p['name'], 'url': p['url']}
                                 for p in pages if p['status'] == 'read']}


DECISION = re.compile(r'^\s*(?:[-•*]\s*)?(?:결정(?:사항)?|확정(?:사항)?|합의(?:사항)?)\s*[:：]\s*(.+)$')
PROPOSAL = re.compile(r'^\s*(?:[-•*]\s*)?(?:제안(?:사항)?|논의(?:사항)?|검토(?:사항)?)\s*[:：]\s*(.+)$')


def draft_minutes(documents):
    """Extract only explicitly labelled source statements; never infer votes."""
    decisions, proposals, used = [], [], []
    for row in documents[:5]:
        url = row.get('url', '')
        if not isinstance(url, str) or not url.startswith('https://'):
            continue
        content = row.get('content', '')
        if not isinstance(content, str):
            continue
        used.append({'kind': row.get('kind'), 'title': str(row.get('title', ''))[:120], 'url': url})
        section = None
        for line in content[:6000].splitlines():
            heading = re.sub(r'^[\s#*]+|[\s#*]+$', '', line)
            if re.fullmatch(r'결정\s*사항|확정\s*사항|합의\s*사항', heading):
                section = decisions
                continue
            if re.fullmatch(r'제안\s*사항|논의\s*사항|검토\s*사항|제안[·/]논의\s*사항', heading):
                section = proposals
                continue
            if line.lstrip().startswith('#'):
                section = None
            for pattern, target in ((DECISION, decisions), (PROPOSAL, proposals)):
                match = pattern.match(line)
                if match and match.group(1).strip():
                    value = re.sub(r'\s+', ' ', match.group(1)).strip()[:240]
                    if len(target) < 8 and not any(x['text'] == value and x['url'] == url for x in target):
                        target.append({'text': value, 'url': url})
                    break
            else:
                bullet = re.match(r'^\s*[-•*]\s+(.+)$', line)
                if section is not None and bullet and len(section) < 8:
                    value = re.sub(r'\s+', ' ', bullet.group(1)).strip()[:240]
                    if value and not any(x['text'] == value and x['url'] == url for x in section):
                        section.append({'text': value, 'url': url})
                elif line.strip() and not bullet:
                    section = None
    def lines(rows):
        return '\n'.join(f"- {r['text']} ([출처]({r['url']}))" for r in rows) if rows else '- 원문에서 명시적으로 확인되지 않았어요.'
    body = ('# 회의록 초안\n\n## 결정 사항\n' + lines(decisions)
            + '\n\n## 제안·논의 사항\n' + lines(proposals)
            + '\n\n## 확인 필요\n- 참석자·일시·담당자·기한은 근거 원문에서 별도 확인해 주세요.')
    return {'draft': body, 'decisions': decisions, 'proposals': proposals,
            'citation_sources': used,
            'limits': '원문의 명시적 결정·제안 문장만 옮긴 한국어 초안이에요. 누락된 참석자·일시·가격을 만들어 넣지 않았어요.'}
