"""Bounded, read-only public sources. Browser text is evidence, never instructions."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import PurePosixPath
import re
import time
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json

NOTION_ROOT = "https://magical-sovereign-7b8.notion.site/3ecfffe60c2680338df5d659cd0f7bea"
GITHUB_ORG = "GuJeuk-Check-in"


def now():
    return datetime.now(timezone.utc).isoformat()


def page_id(url):
    parsed = urlparse(url)
    selected = parse_qs(parsed.query).get('p', [''])[0]
    if re.fullmatch(r'[a-f0-9]{32}', selected):
        return selected
    match = re.search(r"([a-f0-9]{32})(?:[/?#]|$)", parsed.path.replace("-", ""))
    return match.group(1) if match else ""


def notion_url(url):
    parsed = urlparse(url)
    root = urlparse(NOTION_ROOT)
    path_id = page_id(parsed._replace(query='', fragment='').geturl())
    if parsed.scheme != "https" or parsed.netloc != root.netloc or not path_id:
        raise ValueError("공개한 팀 Notion 사이트만 읽을 수 있어요")
    query = parse_qs(parsed.query)
    selected = query.get('p', [''])[0]
    suffix = ''
    if selected:
        if not re.fullmatch(r'[a-f0-9]{32}', selected):
            raise ValueError('invalid selected Notion page')
        suffix = '?' + urlencode({'p': selected, 'pm': 's'})
    # Preserve the actual published slug. Dropping it invents a different URL.
    return f"https://{root.netloc}{parsed.path.rstrip('/')}" + suffix


def safe_path(path):
    parts = PurePosixPath(path.lower()).parts
    return not any(p in {"env", "secrets", "credentials", ".git", "node_modules"}
                   or p.startswith(".env") or p.endswith((".pem", ".key", ".p12", ".sqlite", ".sql")) for p in parts)


def document(kind, ident, url, title, content, **extra):
    # Conservative literal-secret screen; not a security certification. Entire
    # documents are refused, rather than storing a partially redacted credential.
    if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:gh[pousr]_|github_pat_|sk-(?:proj-)?)[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}', content):
        raise ValueError('credential_shaped_content_excluded')
    return {"kind": kind, "id": str(ident), "url": url, "title": title[:500],
            "content": content[:24000], "observed_at": now(), **extra}


def collect_notion(url=NOTION_ROOT, *, max_pages=40, reader=None, start=0, max_seconds=300):
    """Walk public links, not private APIs. View pagination/lazy content stays explicit.

    reader(url)->(title, body, links) permits deterministic offline tests.
    start rotates a bounded crawl across runs (the root is always visited).
    """
    root = notion_url(url)
    if not 1 <= max_pages <= 200 or not 30 <= max_seconds <= 900:
        raise ValueError("invalid page bound")
    docs, issues, queue, visited = [], [], [root], set()
    deadline = time.monotonic() + max_seconds
    manager = browser = None
    try:
        if reader is None:
            from playwright.sync_api import sync_playwright
            manager = sync_playwright().start()
            kwargs = {"headless": True, "args": ["--disable-dev-shm-usage"]}
            executable = os.environ.get("GUJEUK_BROWSER")
            if executable:
                kwargs["executable_path"] = executable
            browser = manager.chromium.launch(**kwargs)
            def browser_reader(target):
                nonlocal browser
                assert browser is not None and manager is not None
                # Isolate each page and release its renderers/cache. A crashed
                # browser must not poison every remaining page in this crawl.
                if not browser.is_connected():
                    browser = manager.chromium.launch(**kwargs)
                context = browser.new_context()
                try:
                    page = context.new_page()
                    def scope_navigation(route):
                        if route.request.is_navigation_request() and route.request.frame == page.main_frame:
                            try:
                                notion_url(route.request.url)
                            except ValueError:
                                route.abort()
                                return
                        route.continue_()
                    page.route('**/*', scope_navigation)
                    budget = lambda: max(1, min(45000, int((deadline - time.monotonic()) * 1000)))
                    response = page.goto(target, wait_until="domcontentloaded", timeout=budget())
                    if response is None or response.status >= 400:
                        raise SourceHTTPError(response.status if response else 0)
                    page.wait_for_function("""() =>
                        document.querySelector('.notion-page-content, .notion-collection-view') &&
                        document.title.trim() && document.title.trim() !== 'Notion' &&
                        document.body.innerText.trim().length >= 40
                    """, timeout=min(25000, budget()))
                    notion_url(page.url)  # Reject a login or off-site redirect.
                    page.wait_for_timeout(1500)
                    texts, discovered, previous, stable = [], {}, None, 0
                    # Collect each visible slice BEFORE virtualized rows disappear.
                    for _ in range(40):
                        visible = page.locator('body').inner_text()
                        if visible not in texts:
                            texts.append(visible)
                        for link in page.locator('a[href]').evaluate_all('xs=>xs.map(x=>({url:x.href,title:x.innerText.slice(0,200)}))'):
                            discovered[link['url']] = link
                        position = page.evaluate('''() => {
                            let candidates = [...document.querySelectorAll('*')].filter(x =>
                                x.clientHeight > 200 && x.scrollHeight > x.clientHeight + 20 &&
                                /auto|scroll/.test(getComputedStyle(x).overflowY));
                            let el = candidates.sort((a,b)=>b.clientHeight-a.clientHeight)[0] || document.scrollingElement;
                            el.scrollTop += Math.max(500, el.clientHeight * .8);
                            return [el.scrollTop, el.scrollHeight, el.clientHeight];
                        }''')
                        marker = (tuple(position), len(discovered), len(visible))
                        stable = stable + 1 if marker == previous else 0
                        previous = marker
                        if stable >= 3 or time.monotonic() >= deadline - 2:
                            break
                        page.wait_for_timeout(200)
                    return page.title(), '\n\n'.join(texts), list(discovered.values())
                finally:
                    if browser.is_connected():
                        context.close()
            reader = browser_reader
        while queue and len(visited) < max_pages and time.monotonic() < deadline:
            target = queue.pop(0)
            if target in visited:
                continue
            visited.add(target)
            try:
                title, body, links = reader(target)
                if len(body.strip()) < 40 or "JavaScript must be enabled" in body or title.strip() in {"Notion", ""}:
                    raise ValueError("notion_shell_or_denied")
                candidates, child_links = [], []
                for link in links:
                    try:
                        raw_url = link.get('url', '') if isinstance(link, dict) else link
                        if not safe_path(urlparse(raw_url).path):
                            continue
                        child = notion_url(raw_url)
                    except (ValueError, TypeError, AttributeError):
                        continue
                    if child != target and len(child_links) < 120:
                        child_links.append({'url': child, 'title': str(link.get('title', ''))[:200] if isinstance(link, dict) else ''})
                    if child not in visited and child not in queue and child not in candidates:
                        candidates.append(child)
                if len(body) > 100000:
                    issues.append({'url': target, 'reason': 'rendered_text_budget_exceeded'})
                for offset in range(0, min(len(body), 100000), 18000):
                    ident = page_id(target) + (':' + str(offset) if offset else '')
                    docs.append(document('notion', ident, target, title, body[offset:offset+18000],
                        links=child_links if offset == 0 else [], content_truncated=len(body) > 100000,
                        chunk_offset=offset, rendered_characters=len(body)))
                if target == root and candidates:
                    offset = start % len(candidates)
                    candidates = candidates[offset:] + candidates[:offset]
                queue.extend(candidates)
            except Exception as error:
                issues.append({"url": target, "error": type(error).__name__,
                    'status': getattr(error, 'status', None),
                    'reason': str(error) if str(error) in {'notion_shell_or_denied', 'credential_shaped_content_excluded'} else ''})
                if getattr(error, 'status', None) == 429:
                    break  # Never hammer the remaining pages after a rate limit.
        return {"documents": docs, "coverage": {"kind": "notion", "checked": now(),
                "visited": len(visited), "remaining_links": len(set(queue) - visited), "issues": issues,
                'time_budget_exhausted': bool(queue) and time.monotonic() >= deadline,
                "complete": False, "limits": ["공개 페이지 DOM만 읽어요", "DB 필터·숨김 속성·페이지네이션·첨부 전체 수집은 보장하지 않아요",
                "공개되지 않은 페이지는 읽지 않아요", "크롤링 한도는 다음 주기에 시작 순서를 바꿔 보완해요"]}}
    finally:
        if browser:
            browser.close()
        if manager:
            manager.stop()


class SourceHTTPError(ValueError):
    def __init__(self, status, retry_at: float = 0):
        super().__init__('source_http_' + str(status))
        self.status, self.retry_at = status, retry_at


_github_reset = 0


def github_get(path):
    global _github_reset
    if not path.startswith((f"/orgs/{GITHUB_ORG}/", f"/repos/{GITHUB_ORG}/")) or ".." in path:
        raise ValueError("organization scope")
    request = Request("https://api.github.com" + path, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": "gujeuk-pm", "X-GitHub-Api-Version": "2022-11-28"})
    if time.time() < _github_reset:
        raise SourceHTTPError(429, _github_reset)
    try:
        with urlopen(request, timeout=30) as response:
            if response.headers.get('X-RateLimit-Remaining') == '0':
                _github_reset = float(response.headers.get('X-RateLimit-Reset', 0))
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("response too large")
            return json.loads(raw)
    except HTTPError as error:
        if error.headers.get('X-RateLimit-Remaining') == '0':
            _github_reset = float(error.headers.get('X-RateLimit-Reset', 0))
        raise SourceHTTPError(error.code, _github_reset) from None


def collect_github(*, get=github_get, max_repos=3, max_prs=1, start=0):
    docs, issues = [], []
    try:
        repos = get(f"/orgs/{GITHUB_ORG}/repos?per_page=100&sort=pushed")
    except Exception as error:
        return {"documents": [], "coverage": {"kind": "github", "checked": now(),
            "issues": [{'error': type(error).__name__, 'status': getattr(error, 'status', None),
                        'retry_at': getattr(error, 'retry_at', 0)}],
            'next_start': start, "complete": False}}
    offset = start % len(repos) if repos else 0
    ordered = repos[offset:] + repos[:offset]
    for repo in ordered[:max_repos]:
        name = repo.get("name", "")
        if not re.fullmatch(r"[\w.-]+", name) or repo.get("private"):
            continue
        base = f"/repos/{GITHUB_ORG}/{name}"
        try:
            rows = get(base + "/issues?state=all&sort=updated&direction=desc&per_page=30")
            for row in rows:
                docs.append(document("github", f"{name}/issues/{row['number']}", row["html_url"], row["title"],
                    json.dumps({k: row.get(k) for k in ("body", "state", "updated_at", "assignees", "labels", "pull_request")}, ensure_ascii=False)))
            pulls = get(base + f"/pulls?state=all&sort=updated&direction=desc&per_page={max_prs}")
            for pull in pulls:
                detail = get(base + f"/pulls/{pull['number']}")
                files = get(base + f"/pulls/{pull['number']}/files?per_page=30")
                patches = [{"path": f["filename"], "patch": f.get("patch", "")[:8000]}
                           for f in files if safe_path(f["filename"])]
                docs.append(document("github", f"{name}/pulls/{pull['number']}", pull["html_url"], pull["title"],
                    json.dumps({"body": detail.get("body"), "state": detail.get("state"), "merged_at": detail.get("merged_at"),
                                "head_sha": detail.get("head", {}).get("sha"), "files": patches}, ensure_ascii=False),
                    revision=detail.get("head", {}).get("sha", "")))
                time.sleep(0.1)
        except Exception as error:
            issues.append({"repository": name, "error": type(error).__name__,
                           'status': getattr(error, 'status', None), 'retry_at': getattr(error, 'retry_at', 0)})
    return {"documents": docs, "coverage": {"kind": "github", "checked": now(), "issues": issues,
            'next_start': start + max_repos, 'repository_limit': max_repos, 'pr_limit_per_repository': max_prs,
            "complete": False, "limits": ["최근 저장소/이슈/PR 및 PR당 30개 파일만 조회해요", "머지는 배포·업무 완료 증거가 아니에요",
            "전체 코드·리뷰·CI·배포 여부를 검증한 것은 아니에요", "공개 API 요청 한도가 적용돼요"]}}
