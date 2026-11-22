# 운영·배포

## 코드 이력
EC2의 실제 운영 소스를 가져와 기능별로 최초 커밋을 만들었어요. 원본 Git 이력이
없던 기간의 가짜 날짜·가짜 커밋을 만들지 않아요. 최초 파일 해시는
`ec2-import-manifest.json`에 있어요. 개인 일정 UI는 이전 저장소에 남아요.

## GitHub → EC2 Docker
`main` push → Agent CI(오프라인 테스트·비밀 검사·두 Docker 이미지 빌드·실제 Hermes
세션 직렬화) → EC2가 해당 push SHA의 CI 성공을 확인 → 불변 release 디렉터리 →
실행 입력의 내용 비교 → 필요한 이미지 빌드·서비스만 갱신해요. 실제 실행 변경이
있을 때만 두 SQLite 온라인 백업을 먼저 수행해요.

EC2 관리 checkout은 `/home/ubuntu/gujeuk-pm/code`, 실제 실행 소스는
`/home/ubuntu/gujeuk-pm/releases/<40자리 commit SHA>`예요. 실행 중인 소스 위에
`git pull`하지 않아요. `deployed.json`의 `revision`은 마지막으로 반영한 CI 통과
커밋이고, `services.<서비스>.release`와 `image`는 각각 실제 실행 코드·이미지예요.
문서만 바뀌면 검증한 커밋은 전진하지만 실행 중인 코드 경로는 바뀌지 않아요.
외부 서비스·Docker volume·실제 DB·프로필·토큰은 지우지 않아요. 자동 Docker
prune/volume 정리도 없어요. 기존 `gujeuk-pm-worker:local`은 Playwright가 있는
검증된 의존성 이미지로 재사용하고, 실행 Python 소스는 매 release에 고정해요.
Dockerfile·pyproject·lock 변경 시 worker 이미지를 자동으로 새 SHA 태그로 빌드해요.

### 불필요한 컨테이너 교체 방지
- `scripts/deploy_plan.py`가 파일 이름·내용과 이미지 입력을 해시해요. 문서·테스트·
  Figma 등록 도구만 바뀌면 Docker 빌드·재생성·DB 백업을 하지 않아요.
- 서비스별 불변 소스 경로를 유지해요. 단순히 `--force-recreate`만 제거하면
  매 커밋마다 바뀌는 bind 경로 때문에 여전히 전부 교체되는 문제를 피했어요.
- Hermes 패치 이미지와 worker 의존성 이미지는 입력이 바뀔 때만 빌드해요.
  collector 수정은 collector/worker, wiki 수정은 Hermes/worker만 갱신해요.
- 게이트웨이 재시작 전, 응답 중인 세션이나 실행 중인 wiki가 확인되면 다음
  배포 주기로 미뤄요. 이미지 빌드 후에도 다시 검사해요. 이 검사는 admission을
  잠그는 원자적 drain은 아니므로 새 질문과의 모든 경합을 방지한다고 주장하지 않아요.
- 컨테이너가 실행 중이고 재시작/OOM 상태가 아닌지 확인해요. Hermes 교체 시에는
  새로 작성된 게이트웨이 상태와 Discord 연결까지 확인한 뒤 성공 기록을 교체해요.
  실패 시 성공 SHA를 전진시키지 않으며, 데이터 자동 복원·삭제는 하지 않아요.
- 기존 기록에 해시가 없으면 이전 release의 실제 파일과 비교해요. 최초 서비스별
  경로 도입 시 설정 변경에 따른 한 번의 교체는 필요할 수 있어요.
- `deploy.py --revision <SHA> --plan`은 불변 release와 계획만 준비하고 Docker나
  DB를 변경하지 않아요. 운영 중 main의 정확한 SHA/CI 확인은 pull_deploy가 담당해요.

에이전트는 gateway·Discord collector·업무 worker·Notion browser reader 4개예요.
분리된 browser는 업무 DB·모델/Discord 자격 증명을 마운트하지 않아요. reader와
worker는 같은 의존성 이미지를 공유해요. 컨테이너 수와 이미지 레이어 사용량은
같은 개념이 아니며, 종료 이력의 마지막 메모리 값은 현재 사용량이 아니에요.
모니터 화면의 같은 이름 EXITED/UP만으로 실제 중복 실행을 판단하지 않아요.
기존 Caddy·Redis·다른 애플리케이션은 이 배포 대상이 아니에요.

실제 CPU/메모리 peak와 호스트 여유를 측정하기 전에는 한도를 임의로 낮추거나
서비스를 합치지 않아요. 이번 변경에 중지 컨테이너·이미지·캐시·볼륨·백업 삭제나
자동 정리는 포함하지 않아요. 기존 종료 컨테이너가 남았는지는 `docker ps -a`와
Compose 라벨로 확인하고, 정리 대상과 보존 범위를 별도 승인받아야 해요.

운영 관리자가 승인한 main만 실행해요. PR 코드를 EC2에서 실행하지 않아요.
프로덕션 SSH 키를 GitHub Actions에 제공하거나 공개 PR에 self-hosted runner를
노출하지 않아요. 첫 설치 후 `burserker-deploy.timer`가 2분 간격으로 확인해요.
GitHub API의 quota/secondary 403·429는 `deploy-ci.json`에 재시도 시각을
보존하고, 그 전에는 새 main이 생겨도 API를 다시 호출하지 않아요. 인증/접근
403과 quota를 구분하며 정확한 SHA의 성공 CI를 우회하지 않아요. CI 대기나
backoff로 oneshot이 종료된 것은 배포 완료가 아니므로 `deployed.json`을 확인해요.

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
  Mac 등록 실행은 `uv run --frozen --extra pm-server python scripts/enroll_figma.py`를
  사용해요. Terminal의 일반 `python3`는 프로젝트 Python과 다를 수 있어요.
  `URLError` 안의 `SSLCertVerificationError`는 토큰 오류가 아니라 해당 Python의
  인증서 문제예요. TLS 검증을 끄지 말고 프로젝트 환경의 연결부터 확인해요.
  수집기는 GitHub/Figma의 429 `retry_at`을 재시작 뒤에도 지켜요. 대기 중에는
  마지막 실제 확인 시각·오류를 보존하고 다른 출처는 계속 처리해요.
  `scripts/verify_figma.py`의 metadata 성공은 지정 노드 본문 수집 성공과 달라요.

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

`burserker-wiki.timer`가 새 문서/수정 문서를 최대 24개씩, 독립 SDK agent
2개로 처리하고 완료 후 30초 쉬어요. SQLite/파일 쓰기는 메인 스레드만 하고,
출처 원문을 한 프롬프트에 섞지 않아요. 빈 원문은 LLM 호출 없이 raw와 처리
기록을 보존하고, 출처 종류를 번갈아 처리하며 변경 없는 파일은 다시 쓰지 않아요. LLM에는
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
