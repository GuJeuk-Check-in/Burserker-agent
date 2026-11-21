"""Restart-safe public-site frontier, shared with authorized on-demand reads."""
from .sources import NOTION_ROOT, now
from .notion_reader import request_page


def crawl(store, state, *, reader=request_page, max_pages=3):
    store.seed_notion_cache()
    store.schedule_notion([NOTION_ROOT])
    docs, issues = [], []
    for _ in range(max_pages):
        url = store.due_notion()
        if not url:
            break
        result = reader(state, url)
        result['requested_url'] = url
        store.record_notion_read(result)
        docs.extend(result.get('documents', []))
        failures = result.get('coverage', {}).get('issues', [])
        issues.extend(failures)
        if not result.get('documents'):
            if not failures:
                issues.append({'url': url, 'reason': result.get('error', 'source_read_failed')})
            break  # Do not hammer another page after a blocked request.
    frontier = store.notion_frontier_status()
    if frontier.get('failures'):
        issues.append({'reason': 'known_pages_awaiting_retry', 'failures': frontier['failures']})
    return {'documents': docs, 'coverage': {'kind': 'notion', 'checked': now(),
        'frontier': frontier, 'issues': issues, 'complete': False,
        'limits': ['공개 DOM에 발견된 링크를 지속 탐색해요. 사이트 전체의 숨겨진 행/첨부/권한 제한은 완전 수집으로 간주하지 않아요.']}}
