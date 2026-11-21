# 운영·배포

## 코드 이력
EC2의 실제 운영 소스를 가져와 기능별로 최초 커밋을 만들었어요. 원본 Git 이력이
없던 기간의 가짜 날짜·가짜 커밋을 만들지 않아요. 최초 파일 해시는
`ec2-import-manifest.json`에 있어요. 개인 일정 UI는 이전 저장소에 남아요.

## GitHub → EC2 Docker
`main` push → Agent CI(오프라인 테스트·비밀 검사·두 Docker 이미지 빌드·실제 Hermes
세션 직렬화) → EC2가 해당 push SHA의 CI 성공을 확인 → 불변 release 디렉터리 →
두 SQLite 온라인 백업 → 핀 고정 Hermes 패치 이미지 빌드 → 에이전트 서비스만 재생성해요.

EC2 관리 checkout은 `/home/ubuntu/gujeuk-pm/code`, 실제 실행 소스는
`/home/ubuntu/gujeuk-pm/releases/<40자리 commit SHA>`예요. 실행 중인 소스 위에
`git pull`하지 않아요. `deployed.json`이 실행 SHA·이미지·코드 경로를 기록해요.
외부 서비스·Docker volume·실제 DB·프로필·토큰은 지우지 않아요. 자동 Docker
prune/volume 정리도 없어요. 기존 `gujeuk-pm-worker:local`은 Playwright가 있는
검증된 의존성 이미지로 재사용하고, 실행 Python 소스는 매 release에 고정해요.
의존성 변경 시 CI와 동일한 Dockerfile로 worker 이미지를 새로 빌드해야 해요.

운영 관리자가 승인한 main만 실행해요. PR 코드를 EC2에서 실행하지 않아요.
프로덕션 SSH 키를 GitHub Actions에 제공하거나 공개 PR에 self-hosted runner를
노출하지 않아요. 첫 설치 후 `burserker-deploy.timer`가 2분 간격으로 확인해요.

## 읽기 범위와 빠짐 추적
- Discord: 구즉 guild만, 타 guild/DM 제외. 최근 증분+누락 복구와 전체 과거 역방향
  커서를 분리해 재시작 후 계속해요. 채널·접근 가능한 스레드·보관 스레드 페이지를
  추적해요. 투표·임베드·첨부 메타데이터도 남겨요. 첨부 파일 본문/이미지/OCR·봇이
  볼 수 없는 비공개 스레드는 읽었다고 표시하지 않아요.
- Notion: 지정 공개 사이트만. on-demand 실제 읽기 결과도 저장하고 발견된 링크를
  영속 frontier에 추가해요. 읽은 페이지·대기·실패·429 재시도를 구분해요.
  공개 링크로 발견 불가능한 행/뷰/페이지, 로그인 전용 데이터, 렌더링 상한은
  완전 수집으로 처리하지 않아요. 시작 화면이나 DB 링크만 보고 내용을 읽었다고
  답하지 않아요. 원래 공개 slug를 보존해 잘못 재구성한 URL도 피하세요.
- Figma: 지정한 파일 `VHUfbJB75st06zIBdIIRJT`과 노드 `9353:163`만 읽어요.
  `file_content:read`(메타데이터 조회 시 `file_metadata:read`) 토큰을 소유자가
  `scripts/enroll_figma.py`의 숨김 입력으로 등록해요. 채팅/명령행 인자에 토큰을
  붙이지 않아요. 파일 노드·텍스트·구조/스타일을 수집하며 렌더 이미지·댓글을
  읽었다고 주장하지 않아요. 토큰 미등록/권한/429는 coverage에 표시해요.

Discord 원문과 wiki 검색은 요청자의 현재 View Channel/Read Message History,
비공개 스레드 멤버십을 확인해요. 여러 출처의 위키 파일 전체를 팀에 그대로
노출하지 않고, 접근 가능한 원문별 주장만 반환해요. 기존 업무 변경 승인/역할
정책은 그대로예요.

## 대화
첫 질문은 봇/봇의 managed role 멘션, 스레드 후속은 멘션 불필요예요.
바쁜 대화의 새 질문은 `request_lane`으로 분기하고 actor/guild/thread/delivery
식별자는 바꾸지 않아요. 같은 대화의 동시 분기는 최대 3개, 전체 세션 상한은
4개예요. 같은 대화의 분기 한도를 넘으면 기존 queue로 받아 첫 작업을 steer하지
않아요. 전역 자원 한도에 걸린 다른 대화는 Hermes의 용량 제한을 받을 수 있어요.
upstream 패치 문맥이 바뀌면 Docker 빌드가 실패해서 자동으로 덮어쓰지 않아요.

## 비공개 LLM wiki
참고 원문: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

실제 위치는 `data/pm-workspace/wiki/`예요. `raw/` 불변 원본 버전,
분야별/인물별 엔티티 페이지, `index.md`, `log.md`, `AGENTS.md`,
`coverage.json`을 자동 생성해요. 코드 저장소는 `wiki/`와 실제 데이터 디렉터리를
Git/Docker context에서 제외해요. 개인 SecondBrain은 복제하지 않아요.

`burserker-wiki.timer`가 새 문서/수정 문서를 최대 8개씩 처리해요. LLM에는
도구·프로필 메모리·프로젝트 지침을 제공하지 않아요. 모델은 경로나 코드를 쓰지
않고 고정 JSON 주장만 반환해요. 프로그램이 원문 그대로의 인용을 검증한 뒤
안전한 해시 경로에 원자적으로 써요. 원문이 바뀌면 그 원문의 이전 주장을
교체하고 변경 이력을 DB에 보존해요. 실패는 15분 뒤 재시도해요.

인물 정보는 Discord 작성자 자신의 명시적인 업무/소통 선호·역할만 허용해요.
말투에서 심리/성격·건강·정치·종교 등을 추측하지 않아요. 사실·추론과 원문 시점을
표시하고, 초기 처리 대기와 실패 건수를 숨기지 않아요. 원본 자료가 미수집이거나
위키 처리 대기가 있는데 '모든 정보 구축 완료'라고 답하면 안 돼요.

## 점검
개발: `uv run --extra pm-server pytest -q`, `python3 scripts/security_check.py`.
운영: `systemctl status burserker-deploy.timer burserker-wiki.timer`,
`journalctl -u burserker-deploy -u burserker-wiki`, `deployed.json`,
`wiki_status`와 `sources` coverage를 확인해요. 비밀값/실제 원문을 공개 로그나
GitHub 이슈에 붙이지 마세요.
