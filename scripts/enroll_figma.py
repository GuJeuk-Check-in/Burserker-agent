"""Run on the owner's Mac: masked input, validate read access, SSH stdin enrollment.
Never pass a token as a command argument, commit it, or paste it into a chat.
"""
import getpass
import json
import subprocess
import sys
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirect_rejected')


def main():
    print('버서커 Figma 읽기 연결 · 토큰은 화면/채팅/명령 이력에 표시되지 않아요.')
    print('Figma 계정 설정 → 보안(Security) → Personal access tokens에서 읽기 토큰을 발급해 주세요.')
    print('필수 범위: file_content:read · file_metadata:read, 선택: file_comments:read')
    token = getpass.getpass('Figma 토큰을 붙여 넣고 Enter (빈 입력은 취소): ').strip()
    if not token:
        print('취소했어요.'); return 1
    try:
        url='https://api.figma.com/v1/files/VHUfbJB75st06zIBdIIRJT?depth=1'
        with build_opener(NoRedirect()).open(Request(url,headers={'X-Figma-Token':token}),timeout=30) as response:
            data=json.loads(response.read(512*1024))
        if 'document' not in data:
            raise ValueError('invalid_file_result')
    except HTTPError as error:
        print(f'읽기 확인 실패: HTTP {error.code}. 토큰 범위와 파일 접근 권한을 확인해 주세요.')
        return 1
    except Exception as error:
        print('읽기 확인 실패: '+type(error).__name__); return 1
    remote="""import json,os,sys
from pathlib import Path
value=json.load(sys.stdin)['token']
folder=Path('/home/ubuntu/gujeuk-pm/data/pm-figma')
if folder.is_symlink():raise ValueError('unsafe_directory')
folder.mkdir(mode=0o700,exist_ok=True);folder.chmod(0o700)
p=folder/'token'
if p.is_symlink():raise ValueError('unsafe_file')
tmp=folder/('token.'+str(os.getpid()))
fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:f.write(value+'\\n')
os.replace(tmp,p)
print('figma_credential_enrolled')
"""
    import shlex
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
        '-o','ConnectTimeout=10','ubuntu@3.37.79.125','python3 -c '+shlex.quote(remote)],
        input=json.dumps({'token':token}),text=True,capture_output=True,timeout=45)
    token=''
    if result.returncode or 'figma_credential_enrolled' not in result.stdout:
        print('서버 저장에 실패했어요. SSH 연결을 확인해 주세요. 토큰은 출력하지 않아요.');return 1
    print('파일 읽기 권한 확인과 서버 보안 저장을 마쳤어요. 다음 수집 주기에 자동 반영돼요.')
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:print('\n취소했어요.');raise SystemExit(1)
