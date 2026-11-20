# Burserker Agent

구즉 팀의 Discord 업무 에이전트와 서버 수집·근거 검색·위키를 관리해요.
개인 일정 앱/개인 SecondBrain과 분리된 저장소예요.

- 소스: `src/oijwef098234/gujeuk_ai` (기존 SQLite/SSH 프로토콜 호환 namespace)
- Discord 수집·Hermes 플러그인·운영: `services/gujeuk_pm`
- EC2에 실제 있던 코드의 파일별 원본 SHA: `docs/ec2-import-manifest.json`
- 비밀값, 실제 대화 DB, raw 자료, wiki/는 Git/Docker build context에서 제외해요.
- 원본의 기록이 없던 기간을 과거 커밋으로 위장하지 않아요. 이 저장소의 커밋은
  실제 서버 스냅샷을 기능별로 가져오고 이후 변경을 기록한 이력이에요.

개발 검증: `uv run --extra pm-server pytest -q`

노션 공개 페이지를 읽는 것과 워크스페이스 전체를 빠짐없이 읽는 것은 달라요.
권한 거부/요청 제한/첨부·숨겨진 DB/아직 읽지 않은 페이지는 coverage에 남기고
완전 수집했다고 말하지 않아요. Discord도 접근 가능한 채널/스레드 범위만 읽어요.
