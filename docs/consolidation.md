# 단일 컨테이너 운영

현재 목표는 `gujeuk-pm-hermes` 하나다. 기존 Hermes 공식 PID 1/s6 진입점과 Discord gateway를 유지하고, 추가 s6 서비스 `pm-workers`가 collector/worker/reader 세 프로세스를 감독한다. child 재시작은 지수 backoff, 종료는 프로세스 그룹 단위다. 상태는 `/run/burserker-status.json`에 본문/비밀 없는 PID·시작 식별자·재시작 수만 기록한다. Docker health는 세 child 생존을 확인하고, 별도 배포 readiness는 새 Discord 연결도 확인한다. source freshness는 기존 ops health의 별도 검사다.

- SDK Python 환경은 건드리지 않는다. PM/Playwright는 `/opt/pm` 독립 가상환경과 uv.lock의 고정 버전을 쓴다.
- gateway/collector/worker는 hermes UID 1000, browser reader는 UID 1001이다. 자식 환경은 allowlist이며 provider 토큰을 상속하지 않는다.
- reader는 공개 코드 `/release/src`와 `/requests`만 필요하다. 공유 queue는 GID 1002, 디렉터리 2770/파일 660, 일반 DB/인증은 그대로 private이다.
- queue는 같은 호스트 경로 `data/pm-workspace/notion-read`를 사용한다. 나머지 DB/wiki/세션/인증/체크포인트도 기존 `data` bind를 그대로 사용한다.
- Unix 사용자 분리는 별도 컨테이너와 동등한 격리가 아니다. 브라우저 OS 취약점/전체 컨테이너 장애의 영향은 커진다. 프로세스별 메모리 제한 대신 컨테이너 전체 3GiB 제한이 적용된다. 통합 자체가 API quota나 작업 메모리를 제거하지 않는다.

## 전환 순서

1. 로컬 전체 pytest/secret check → commit/push → 정확한 SHA의 Agent CI 성공. CI는 이미지·실제 브라우저·SDK 계약·s6 기동도 확인한다.
2. 관련 타이머의 원래 활성 상태를 기록한다. deploy/wiki/analysis/backup/health 타이머 중 활성인 것만 잠시 정지하고, 대응 oneshot이 inactive/failed가 될 때까지 기다린다. 실패 시 trap으로 원래 활성 타이머만 복구한다. 타 서비스에는 손대지 않는다.
3. 기존 실행 컨테이너·데이터 집계·무관한 컨테이너 ID/StartedAt을 기록한다. `python3 scripts/deploy.py --revision SHA --plan`으로 대상이 hermes 하나인지 확인한다.
4. `python3 scripts/deploy.py --revision SHA` 실행. 이미지 빌드는 기존 서비스가 실행 중인 동안 진행한다. gateway/wiki/analysis/backup idle을 재확인한 뒤, 구 writer 네 개를 정지하고 SQLite 두 개의 일관된 검증 백업을 만든 후 새 컨테이너를 시작한다. 백업 실패/기동 실패에는 이전 이미지·코드로 rollback하며 DB를 과거 백업으로 덮어쓰지 않는다.
5. readiness 성공 후에만 deployed.json을 single로 기록한다. `deployment-history/before-single-SHA.json`에는 이전 실행 메타데이터만 보존한다. volume/image/source data 삭제는 없다.
6. `sudo python3 scripts/install_timers.py`로 관리 unit을 갱신한다. health/backup service만 새 위치로 바꾸고 해당 알림 타이머를 새로 활성화하지 않는다. deploy/wiki는 기존 자동 운영을 복구한다.
7. 아래 실제 런타임 검증, source freshness, 모델 응답 및 수집 진행을 확인한다. 구 collector/worker/reader가 모두 정지 상태인지 확인한 후 정확히 그 세 컨테이너만 `docker rm`으로 retire한다. `-v`, force, prune은 사용하지 않는다. 이미지는 rollback용으로 남긴다.

## 실제 런타임 검증

checkout의 CI 승인된 파일을 stdin으로 전달한다:

`docker exec -i gujeuk-pm-hermes /opt/hermes/.venv/bin/python - --restart-reader < scripts/verify_single_runtime.py`

UID 1001로 Chromium을 실제 렌더링하고 DB/설정/토큰 직접 읽기가 거부되는지 확인한다. queue가 비었을 때 reader만 종료해 내부 자동 재시작도 확인한다. Discord 연결은 상태 메타데이터로 확인하며 이 검증은 팀 채널에 메시지를 보내거나 새 팀원의 종단 질문을 대신하지 않는다.

## 롤백과 후속 배포

`single_deploy.rollback`은 보존된 이전 metadata로 원래 immutable source/image를 다시 시작한다. 첫 전환 검증 전에는 구 컨테이너를 삭제하지 않는다. retire 후 구 구조로 되돌릴 때는 이전 release Compose로 collector/worker/reader도 다시 생성해야 한다. state 경로는 바뀌지 않는다. 모델이 승인하지 않은 업무를 임의 승인하거나 source/wiki/credential을 초기화하지 않는다.

single 이후에는 코드 변경 시 한 컨테이너만 재생성한다. 문서·테스트만 바뀐 커밋은 실제 서비스 mount/image를 유지한다. 기존 분석/wiki/호스트 관리 작업은 여전히 같은 gateway 컨테이너를 실행 대상으로 사용한다.
