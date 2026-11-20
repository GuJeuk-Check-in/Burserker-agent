#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077

printf '\n[1/4] Discord 토큰 등록 및 봇·서버·채널 확인\n'
python3 ./setup-discord.py

printf '\n[2/4] 모델 제공자 로그인 및 모델 선택\n'
printf '현재 사용 중인 계정의 제공자로 로그인하고 gpt-6.1-sol을 선택해 주세요.\n'
printf '토큰·인증 파일은 이 서버의 전용 data 폴더에만 저장돼요.\n'
docker compose run --rm --no-deps hermes model

printf '\n[3/4] 초기 Discord 브라우저 도구 비활성화\n'
docker compose run --rm --no-deps hermes tools disable browser --platform discord

printf '\n모델 로그인과 gpt-6.1-sol 선택을 완료했나요? [y/N] '
read -r answer
case "$answer" in
  y|Y|yes|YES) ;;
  *) printf '게이트웨이는 시작하지 않았어요. 설정을 마친 뒤 다시 실행해 주세요.\n'; exit 0 ;;
esac

printf '\n[4/4] 구즉 PM 전용 게이트웨이 시작\n'
docker compose up -d --no-deps hermes
docker compose ps
printf '\n이제 지정한 보고 채널에서 봇을 멘션하고 연결 확인이라고 보내 주세요.\n'
printf '컨테이너 실행만으로 모델 인증이나 Discord 응답 성공이 보장되지는 않아요. 실제 답변을 확인해야 해요.\n'
printf '토큰·로그 전문·인증 코드는 채팅에 붙이지 마세요. 완료 여부만 알려 주세요.\n'
