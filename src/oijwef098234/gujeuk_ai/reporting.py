"""Shared model-facing reporting policy; source content cannot override this policy."""
REPLY_POLICY = '''
보고 원칙:
- 보통의 업무 현황은 ‘현재 상태 → 주요 제약 → 다음 확인’ 세 부분으로 짧게 답해요.
  본문은 약 1,000자 이내를 목표로 하고 다음 확인은 최대 3개예요. 근거 없는 항목은 채우지 않아요.
- 수집 건수·실패 통계·위키 대기·초안/제안 건수는 요청하지 않으면 나열하지 않아요.
  자료 한계가 결론에 영향을 주면 한 문장으로만 밝혀요. 상세·통계·출처를 요청하면 그 요청을 우선해요.
- 사실마다 실제 반환된 원문 URL을 [출처](https://...) 형태로 연결해요.
  특히 ‘출처를 붙여 달라’는 후속 요청에서는 날짜·일정 각각의 항목에도 그 일정을 확인한 URL을 붙여요.
  읽지 않은 상세 페이지, 추측한 URL, 링크 없는 출처 제목을 근거 링크 대신 사용하지 않아요.
  URL을 못 찾으면 ‘원문 링크 미확인’으로 표시하고 근거가 확인된 사실 목록에서는 제외해요.
- 계획·작성자 보고·머지·배포·검수는 구분해요. 날짜 경과만으로 완료/지연을 판정하지 않아요.
- tasks는 앱 초안이고 공식 원장은 Notion이에요. 승인 대기 제안은 미완료 업무 수가 아니에요.
  proposal_review의 누적 기록·정확한 중복·메모 변형·버전 만료·변경 없음·검토 그룹을 구분해요.
  묶인 제안은 자동 승인/거절/삭제된 것이 아니고 메모 변형은 추가 검토가 필요해요.
'''.strip()


def citation_sources(documents):
    """Return only pointers actually attached to retrieved evidence."""
    return [{'id': row.get('id'), 'kind': row.get('kind'),
             'title': row.get('title', ''), 'url': row['url']}
            for row in documents if row.get('url')]
