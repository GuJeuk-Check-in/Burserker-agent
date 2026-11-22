"""Exercise actual tool-free LLM reporting with existing team sources; no Discord post.

Run only as owner in the approved server profile. Counts/booleans only are printed.
This is a reporting-policy probe, not a complete Discord end-to-end test.
"""
import argparse
from itertools import product
import json
import os
from pathlib import Path
import re
from oijwef098234.gujeuk_ai.inference import ReadOnlyInference
from oijwef098234.gujeuk_ai.reporting import REPLY_POLICY
from oijwef098234.gujeuk_ai.workspace import Workspace


def answer(engine, request, documents, statistics):
    result = engine.ask(REPLY_POLICY + '\n아래 요청에 대한 답변을 JSON {"answer":"답변"}으로 반환하세요.\n'
        + request + '\n<untrusted-data>' + json.dumps({'documents': documents,
            'collection_statistics': statistics}, ensure_ascii=False) + '</untrusted-data>')
    text = result.strip()
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
    return json.loads(text)['answer']


def calendar_documents(rows):
    return [d | {'content': d['content'][:3000]} for d in rows
        if d['kind'] == 'notion' and '구즉' in d['title']
        and re.search(r'(?:^|\n)Date\n', d['content'][:3000])]


def per_item_sources(text, documents):
    blocks = re.split(r'(?m)^\s*(?:[-*•]|\d+[.)])\s+', text)
    if len(blocks) < 3:
        blocks = re.split(r'\n\s*\n', text)
    matches = [[index for index, block in enumerate(blocks) if d['url'] in block
        and re.search(r'\d{1,2}/\d{1,2}|20\d\d-\d\d-\d\d|\d{1,2}\s*월', block)
        ] for d in documents]
    return bool(documents) and any(len(set(indices)) == len(documents) for indices in product(*matches))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    from hermes_cli.config import load_env
    os.environ.update(load_env())
    if not (args.state / 'workspace.db').is_file():
        raise ValueError('existing_workspace_required')
    store = Workspace(args.state / 'workspace.db')
    engine = ReadOnlyInference()
    try:
        # Real Notion calendar pages use an English Date property, not slash dates.
        # A date embedded in an API example or URL is not a calendar fact.
        candidates = calendar_documents(store.documents(500))
        documents = [{k: d[k] for k in ('id', 'kind', 'title', 'url', 'content')} for d in candidates[:3]]
        if len(documents) < 3:
            raise ValueError('need_three_existing_dated_notion_sources')
        documents = [d | {'content': d['content'][:3000]} for d in documents]
        status = store.source_status()
        statistics = {'source_documents': {k: v['documents'] for k, v in status['sources'].items()},
                      'proposal_review': status['proposal_review']['counts']}
        default = answer(engine, '제공된 실제 자료 범위에서 구즉 업무 현황을 짧게 알려줘.', documents, statistics)
        engine.close()
        followup = answer(engine, '제공된 문서 각각에서 확인 가능한 일정·날짜 사실을 하나씩, 총 3개 항목으로 정리하고 '
            '각 항목에 해당 문서의 출처 링크를 붙여줘. 확정 완료로 바꾸지 마.', documents, statistics)

        checks = {
            'default_within_one_discord_message': len(default) <= 2000,
            'default_sections': all(title in default for title in ('현재 상태', '주요 제약', '다음 확인')),
            'default_omits_collection_statistics': not any(word in default for word in ('수집 건수', '대기 제안', '위키 대기')),
            'every_selected_source_cited_per_item': per_item_sources(followup, documents),
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
