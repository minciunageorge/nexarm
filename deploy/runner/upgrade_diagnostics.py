#!/usr/bin/python3
"""Explicit operator-run root upgrade, never authorized to the runner account."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

LEGACY={'readonly.py':'3c1c2b4d2620ba138997734d080403bd175f973f4add6f1430f6af4d0f7182b4',
        'readonly.sudoers':'c27aa3af2dc72986f56ae3c5785e715687edaa4b4d7ba2e2ff050c092fac7251'}
DESTINATIONS={'readonly.py':'/usr/local/lib/nexarm-runner/readonly.py',
              'readonly.sudoers':'/etc/sudoers.d/nexarm-readonly',
              'repository_gate.py':'/usr/local/lib/nexarm-runner/repository_gate.py',
              'container_diagnostics.py':'/usr/local/lib/nexarm-runner/container_diagnostics.py'}


def atomic(path,data,mode):
    fd,name=tempfile.mkstemp(prefix='.nexarm-upgrade-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data);output.flush();os.fsync(output.fileno())
        os.chmod(name,mode);os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def require_known_file(name,current,desired):
    if current!=desired and hashlib.sha256(current).hexdigest()!=LEGACY.get(name):
        raise ValueError('Unknown/custom broker file; preserved')


def main():
    if os.geteuid()!=0 or len(sys.argv)!=2:raise SystemExit('Operator root upgrade only')
    stage=Path(sys.argv[1])
    enrollment=json.load(sys.stdin)  # never logged; token never in argv
    sys.path.insert(0,str(stage))
    from repository_gate import repository_metadata
    repository_metadata(enrollment['repository'],enrollment['token'])
    config_path=Path('/etc/nexarm-runner/readonly.json')
    if config_path.is_symlink() or config_path.stat().st_uid!=0 or config_path.stat().st_mode&0o077:
        raise SystemExit('Unsafe existing broker config; preserved')
    config=json.loads(config_path.read_text())
    if config['container_id']!=enrollment['container_id']:raise SystemExit('Existing enrolled container differs')
    # Validate every file before mutating any; recognize only previous or desired
    # bytes. A partial prior application is resumable, custom files are preserved.
    backups=[]
    for name,destination in DESTINATIONS.items():
        path=Path(destination);desired=(stage/name).read_bytes()
        if os.path.lexists(path):
            info=path.lstat()
            if path.is_symlink() or info.st_uid!=0 or info.st_mode&0o022:raise SystemExit('Unsafe installed broker file')
            current=path.read_bytes()
            require_known_file(name,current,desired)
            backup=path.with_name(path.name+'.pre-diagnostics.bak')
            if current!=desired:
                if os.path.lexists(backup):
                    if backup.is_symlink() or backup.stat().st_uid!=0 or backup.stat().st_mode&0o077 or backup.read_bytes()!=current:
                        raise SystemExit('Existing diagnostic backup differs; preserved')
                else:
                    backups.append((backup,current))
        elif name in LEGACY:raise SystemExit('Expected existing broker/policy missing')
    # Do not grant a new command until privacy/config and implementation are ready.
    token_path=Path('/etc/nexarm-runner/github-metadata.token')
    if os.path.lexists(token_path) and (token_path.is_symlink() or token_path.stat().st_uid!=0 or token_path.stat().st_mode&0o077):
        raise SystemExit('Unsafe existing metadata token; preserved')
    for backup,current in backups:
        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as output:output.write(current)
    atomic(token_path,enrollment['token'].strip().encode()+b'\n',0o600)
    config.update(diagnostics_enabled=True,github_repository=enrollment['repository'])
    atomic(config_path,json.dumps(config,indent=2).encode()+b'\n',0o600)
    for name in ['repository_gate.py','container_diagnostics.py','readonly.py','readonly.sudoers']:
        atomic(Path(DESTINATIONS[name]),(stage/name).read_bytes(),0o440 if name.endswith('sudoers') else 0o644)
    print('Enrolled only private-repository ROS graph / one-frame diagnostics; container and registration preserved.')


if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError):raise SystemExit('Diagnostic upgrade refused; check privacy/API authorization or host files') from None
