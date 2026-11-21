"""Scan committed/source files without ever printing a suspected secret value."""
from pathlib import Path
import re
import subprocess
import sys

PATTERNS=[('private_key',re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\s]{40,}-----END ')),
    ('github_token',re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})')),
    ('aws_key',re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b')),
    ('figma_token',re.compile(r'\bfigd_[A-Za-z0-9_-]{20,}\b')),
    ('provider_key',re.compile(r'\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}\b'))]
FORBIDDEN={'.env','auth.json','discord.db','workspace.db','token'}


def main():
    root=Path(__file__).resolve().parents[1]
    names=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
    issues=[]
    for name in filter(None,names):
        path=Path(name)
        if path.name in FORBIDDEN or any(p in {'wiki','raw','data','memories','sessions','.venv'} for p in path.parts):
            issues.append((name,'forbidden_runtime_path'));continue
        file=root/path
        if not file.exists():continue
        data=file.read_text(errors='replace')
        for label,pattern in PATTERNS:
            if pattern.search(data):issues.append((name,label))
    for name,label in issues:print(name+': '+label)
    print('secret_scan_files='+str(len(list(filter(None,names))))+' findings='+str(len(issues)))
    return bool(issues)


if __name__=='__main__':sys.exit(main())
