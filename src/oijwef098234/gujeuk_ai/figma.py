"""Read-only Figma file/node collector. Credentials never enter documents/logs."""
from pathlib import Path
import json
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode
from urllib.error import HTTPError
from .sources import document, now, SourceHTTPError

FILE_KEY = 'VHUfbJB75st06zIBdIIRJT'
NODE_ID = '9353:163'
ROOT = 'https://www.figma.com/design/' + FILE_KEY
TOKEN_FILE = Path('/run/figma/token')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('figma_redirect_rejected')


def figma_get(path, token):
    if not path.startswith('/v1/files/' + FILE_KEY) or '..' in path:
        raise ValueError('figma_file_scope_rejected')
    request = Request('https://api.figma.com' + path, headers={'X-Figma-Token': token, 'User-Agent': 'BurserkerAgent/1'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=45) as response:
            raw = response.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError('figma_response_limit')
        return json.loads(raw)
    except HTTPError as error:
        retry_at = time.time() + min(86400, max(60, float(error.headers.get('Retry-After', 300)))) if error.code == 429 else 0
        raise SourceHTTPError(error.code, retry_at) from None


def flatten(node):
    yield node
    for child in node.get('children', []):
        yield from flatten(child)


def collect_figma(*, token_file=TOKEN_FILE, get=figma_get, start=0):
    coverage = {'kind': 'figma', 'checked': now(), 'issues': [], 'complete': False,
        'file': FILE_KEY, 'requested_node': NODE_ID,
        'limits': ['파일의 텍스트·노드 속성/구조를 읽어요. 디자인 픽셀/플러그인 비공개 데이터는 별도 검증이 필요해요.']}
    if not token_file.is_file() or not token_file.read_text().strip():
        return {'documents': [], 'coverage': coverage | {'issues': ['credential_missing']}}
    token = token_file.read_text().strip()
    try:
        # No depth limit: retrieve the permitted file tree, including all pages.
        data = get('/v1/files/' + FILE_KEY, token)
        nodes = list(flatten(data['document']))
        nodes.sort(key=lambda n: (n['id'] != NODE_ID, n['id']))
        rows = []
        for node in nodes:
            properties = {k: v for k, v in node.items() if k not in {'children', 'absoluteRenderBounds'}}
            body = json.dumps(properties, ensure_ascii=False, sort_keys=True)
            # Large text/property nodes are chunked, not silently clipped.
            for offset in range(0, len(body), 18000):
                rows.append((node, offset, body[offset:offset+18000]))
        position = start if 0 <= start < len(rows) else 0
        selected = rows[position:position+250]
        docs = [document('figma', n['id'] + ':' + str(offset), ROOT + '?node-id=' + n['id'].replace(':','-'),
            str(n.get('name', n['id'])), text, revision=data.get('version'), node_id=n['id'],
            source_updated_at=data.get('lastModified')) for n, offset, text in selected]
        next_start = position + len(selected)
        return {'documents': docs, 'coverage': coverage | {'nodes_discovered': len(nodes),
            'chunks_discovered': len(rows), 'chunks_read': len(docs),
            'next_start': next_start if next_start < len(rows) else 0,
            'tree_cycle_finished': next_start >= len(rows), 'revision': data.get('version')}}
    except Exception as error:
        return {'documents': [], 'coverage': coverage | {'issues': [{'error': type(error).__name__,
            'status': getattr(error, 'status', None), 'retry_at': getattr(error, 'retry_at', 0)}]}}
