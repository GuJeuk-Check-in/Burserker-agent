import importlib.util
from pathlib import Path


path = Path(__file__).resolve().parents[1] / 'scripts/verify_pm_reporting.py'
spec = importlib.util.spec_from_file_location('reporting_probe', path)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_select_real_calendar_date_property_not_api_example():
    rows = [
        {'kind': 'notion', 'title': '구즉 개발 마감', 'content': 'Date\nNovember 6, 2026', 'url': 'https://example.test/calendar'},
        {'kind': 'notion', 'title': '구즉 API 예시', 'content': '2026-11-06 예시 입력', 'url': 'https://example.test/api'},
        {'kind': 'discord', 'title': '구즉 개발', 'content': 'Date\nNovember 6, 2026', 'url': 'https://example.test/discord'},
    ]
    assert probe.calendar_documents(rows) == rows[:1]


def test_multiline_item_citation_allowed():
    docs = [{'url': 'https://example.test/a'}, {'url': 'https://example.test/b'}, {'url': 'https://example.test/c'}]
    reply = '- 11월 2일 계획\n  출처: https://example.test/a\n- 11월 3일 계획\n  출처: https://example.test/b\n- 11월 4일 계획\n  출처: https://example.test/c'
    assert probe.per_item_sources(reply, docs)


def test_one_bulk_source_footer_does_not_count_as_per_item():
    docs = [{'url': 'https://example.test/a'}, {'url': 'https://example.test/b'}, {'url': 'https://example.test/c'}]
    reply = '11월 2일 계획\n출처: https://example.test/a https://example.test/b https://example.test/c'
    assert not probe.per_item_sources(reply, docs)


def test_missing_source_cannot_pass():
    docs = [{'url': 'https://example.test/a'}, {'url': 'https://example.test/b'}, {'url': 'https://example.test/c'}]
    reply = '- 11/2 계획 https://example.test/a\n- 11/3 계획 https://example.test/b\n- 11/4 계획 링크 미확인'
    assert not probe.per_item_sources(reply, docs)
