# 주간 회의 공지

- 기존 공지 발송과 별개인 **한 번 승인하는 반복 예약**이에요. Discord에 범용 cron/터미널 도구를 열지 않아요.
- PM만 본인이 실제 발송한 공지를 기준으로 예약해요. 대상·멘션·참여 요청·반응은 그 공지를 유지해요.
- 기본: 매주 화요일 낮 12시, Asia/Seoul. weekday 월0~일6, hour/minute로 주간 시각을 지정할 수 있어요.
- schedules → recent_messages에서 실제 reference_draft_id → prepare_schedule(request_id, data={reference_draft_id,policy,weekday,hour,minute}) → preview → 사람이 같은 대화에 `예약 예약ID` → activate_schedule(data={schedule_id}).
- 고정 대상·시간·안건 선정 정책을 한 번 승인해요. 이후 매회 승인은 받지 않아요. 변경은 새 preview/승인이 필요해요. 동일 채널·주간 시각의 본인 기존 예약은 대체하고 중복하지 않아요.
- 본인이 `예약중지 예약ID`를 직접 입력한 뒤 cancel_schedule로 중지해요. 이미 전송 중인 메시지를 회수한다는 뜻은 아니에요.
- 원래 채널 최근 공지 50개와 공개 팀 Notion·공개 GitHub의 법동 관련 근거를 읽고 도구 없는 모델로 안건만 생성해요. 다른 비공개 Discord 채널을 공지에 섞지 않아요. 작성일·관찰일과 현재 한국 시간을 구분하고 기한 경과를 완료로 간주하지 않아요.
- 안건별 실제 출처 URL 및 원문 인용을 검증하고 내부 영수증에 보존해요. 공지에는 번호 안건과 마지막 `기타의견`만 붙이며 근거 설명을 덧붙이지 않아요. 이는 매회 검색·규칙 반영이지 모델 가중치 재학습은 아니에요.
- 실패한 조회/미수집 자료를 읽었다거나 전체 범위를 보장하지 않아요. 근거 없는 안건, 알림 멘션 주입, 잘못된 JSON은 발송하지 않아요.

## 실행·복구

기존 단일 컨테이너를 유지해요. 호스트 gujeuk-pm-dispatch.timer가 매분 검사하고, 실제 시각·권한·중복 판정과 모델/발송은 컨테이너 안에서만 실행해요. 모델은 enabled_toolsets=[]예요. 매회 PM 역할과 채널/멘션/반응 권한을 재검사해요.

상태는 기존 discord-outbox.db의 schedules / schedule_runs에 추가돼요. 기존 백업에 함께 포함되며 재시작 후에도 예약·영수증이 유지돼요. 회차별 단일 SQLite claim/lease와 POST 전 예약, 안정적인 nonce, 실제 read-back, 반응 전 영수증 커밋을 사용해요. 결과 불명확은 자동 재전송하지 않아요. 생성 실패는 5분 간격 최대 3회, 시작 시각부터 30분 이내에만 시도해요. 늦은 공지를 무기한 backfill하지 않으며 등록 이전/이미 지난 오늘 공지는 발송하지 않아요.

운영 CLI(소유자만): `python -m oijwef098234.gujeuk_ai.schedules inspect|preview|dispatch|install`. preview는 실제 읽기/모델을 사용하지만 POST·반응·실행 claim은 하지 않아요. install은 `--reference-draft-id 실제ID --approved`가 필요하고 공개 모델 도구로 노출되지 않아요. 소유자가 이 개발 세션에서 명시적으로 요청한 기존 공지 반복 예약을 등록할 때만 사용해요.

Git/CI/배포 후 대상과 시각을 검증하고, preview가 실제 모델·원문으로 성공한 뒤 `sudo python3 scripts/install_timers.py --enable-schedules`로 검사 타이머를 활성화해요. 예약 등록·권한 점검·미리보기는 실제 예정일의 Discord 전송 성공과 별도 검증이에요. 상태/실패는 schedules와 서비스 journal로 확인하며 전체 로그나 인증정보를 출력하지 않아요.
