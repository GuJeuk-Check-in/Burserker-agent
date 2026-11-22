"""Exercise actual tool-free LLM reporting with existing team sources; no Discord post.

Run only as owner in the approved server profile. Counts/booleans only are printed.
This is a reporting-policy probe, not a complete Discord end-to-end test.
"""
import argparse
import json
from pathlib import Path
import re
from oijwef098234.gujeuk_ai.inference import ReadOnlyInference
from oijwef098234.gujeuk_ai.reporting import REPLY_POLICY
from oijwef098234.gujeuk_ai.workspace import Workspace


def answer(engine, request, documents):
    result = engine.ask(REPLY_POLICY + '\n아래 요청에 대한 답변을 JSON {"answer":"답변"}으로 반환하세요.\n'
        + request + '\n<untrusted-data>' + json.dumps(documents, ensure_ascii=False) + '</untrusted-data>')
    text = result.strip()
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
    return json.loads(text)['answer']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    if not (args.state / 'workspace.db').is_file():
        raise ValueError('existing_workspace_required')
    store = Workspace(args.state / 'workspace.db')
    engine = ReadOnlyInference()
    try:
        candidates = [d for d in store.documents(500) if d['kind'] == 'notion'
            and re.search(r'\d{1,2}/\d{1,2}|20\d\d-\d\d-\d\d', d['title'] + d['content'])]
        documents = [{k: d[k] for k in ('id', 'kind', 'title', 'url', 'content')} for d in candidates[:3]]
        if len(documents) < 3:
            raise ValueError('need_three_existing_dated_notion_sources')
        documents = [d | {'content': d['content'][:3000]} for d in documents]
        default = answer(engine, '제공된 실제 자료 범위에서 구즉 업무 현황을 짧게 알려줘.', documents)
        engine.close()
        followup = answer(engine, '제공된 문서 각각에서 확인 가능한 일정·날짜 사실을 하나씩, 총 3개 항목으로 정리하고 '
            '각 항목에 해당 문서의 출처 링크를 붙여줘. 확정 완료로 바꾸지 마.', documents)
        lines = [line for line in followup.splitlines() if line.strip()]
        checks = {
            'default_within_one_discord_message': len(default) <= 2000,
            'default_sections': all(title in default for title in ('현재 상태', '주요 제약', '다음 확인')),
            'default_omits_collection_statistics': not any(word in default for word in ('수집 건수', '대기 제안', '위키 대기')),
            'every_selected_source_cited_per_item': all(any(d['url'] in line
                and re.search(r'\d{1,2}/\d{1,2}|20\d\d-\d\d-\d\d|\d{1,2}월', line) for line in lines) for d in documents),
        }
        print(json.dumps({'source_documents': len(documents), 'default_chars': len(default),
            'checks': checks, 'discord_posted': False, 'passed': all(checks.values())}))
        if not all(checks.values()):
            raise SystemExit(1)
    finally:
        engine.close()
        store.close()


if __name__ == '__main__':
    main()
