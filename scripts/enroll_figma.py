"""Run on the owner's Mac: masked input, validate read access, SSH stdin enrollment.
Never pass a token as a command argument, commit it, or paste it into a chat.
"""
import getpass
import json
import subprocess
import sys
import ssl
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

MAX_VALIDATION_RESPONSE = 2 * 1024 * 1024
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
       '-o', 'ConnectTimeout=10', 'ubuntu@3.37.79.125']


def ssh_failure(stderr):
    # Never print arbitrary SSH output: credentials only travel through stdin.
    if 'timed out' in stderr or 'Operation timed out' in stderr:
        return '서버 SSH 포트(22) 연결 시간 초과'
    if 'Permission denied' in stderr:
        return 'SSH 계정·키 인증 실패'
    if 'Host key verification failed' in stderr or 'REMOTE HOST IDENTIFICATION HAS CHANGED' in stderr:
        return '서버 호스트 키 확인 실패 (검증을 끄지 마세요)'
    return '서버 SSH 연결 또는 저장 명령 실패'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirect_rejected')


def main():
    print('버서커 Figma 읽기 연결 · 토큰은 화면/채팅/명령 이력에 표시되지 않아요.')
    print('Figma 계정 설정 → 보안(Security) → Personal access tokens에서 읽기 토큰을 발급해 주세요.')
    print('필수 범위: file_content:read · file_metadata:read, 선택: file_comments:read')
    print('토큰을 입력받기 전에 서버 접속부터 확인해요.')
    try:
        probe = subprocess.run(SSH + ['true'], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        print('서버 SSH 접속을 확인하지 못했어요. 토큰은 아직 입력받지 않았어요.')
        return 1
    if probe.returncode:
        print('서버 접속 확인 실패: ' + ssh_failure(probe.stderr))
        print('접속이 복구된 뒤 다시 실행해 주세요. 토큰은 아직 입력받지 않았어요.')
        return 1
    token = getpass.getpass('Figma 토큰을 붙여 넣고 Enter (빈 입력은 취소): ').strip()
    if not token:
        print('취소했어요.'); return 1
    try:
        url='https://api.figma.com/v1/files/VHUfbJB75st06zIBdIIRJT?depth=1'
        with build_opener(NoRedirect()).open(Request(url,headers={'X-Figma-Token':token}),timeout=30) as response:
            raw=response.read(MAX_VALIDATION_RESPONSE + 1)
        if len(raw) > MAX_VALIDATION_RESPONSE:
            raise ValueError('response_too_large')
        data=json.loads(raw)
        if 'document' not in data:
            raise ValueError('invalid_file_result')
    except HTTPError as error:
        print(f'읽기 확인 실패: HTTP {error.code}. 토큰 범위와 파일 접근 권한을 확인해 주세요.')
        return 1
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            print('HTTPS 인증서 검증에 실패했어요. 토큰 입력 문제가 아니라 현재 Python의 인증서 설정 문제예요.')
            print('프로젝트의 uv 실행기로 다시 실행해 주세요: uv run --frozen --extra pm-server python scripts/enroll_figma.py')
        else:
            print('Figma 서버 연결에 실패했어요. 네트워크·프록시·DNS를 확인해야 해요. 토큰이 잘못됐다는 응답은 아니에요.')
        return 1
    except ValueError:
        print('Figma 응답 형식이나 크기를 확인하지 못했어요. 입력을 반복하지 말고 등록 도구를 점검해야 해요.')
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
    try:
        result=subprocess.run(SSH + ['python3 -c '+shlex.quote(remote)],
            input=json.dumps({'token':token}),text=True,capture_output=True,timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        print('Figma 읽기 확인은 통과했지만 서버 저장 연결을 마치지 못했어요. 토큰은 출력하지 않아요.')
        return 1
    finally:
        token=''
    if result.returncode or 'figma_credential_enrolled' not in result.stdout:
        print('Figma 읽기 확인은 통과했지만 서버 저장에 실패했어요: ' + ssh_failure(result.stderr))
        print('토큰을 다시 발급하지 말고 서버 연결·저장 권한을 확인해야 해요.');return 1
    print('파일 읽기 권한 확인과 서버 보안 저장을 마쳤어요. 다음 수집 주기에 자동 반영돼요.')
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:print('\n취소했어요.');raise SystemExit(1)
