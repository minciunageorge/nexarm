#!/usr/bin/python3
"""Explicit operator-run root upgrade, never authorized to the runner account."""
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import tempfile

LEGACY={'readonly.py':{'8255c07b8d4046b1c1f4bec310a7af1a2e1386224ad9845127fee9c325038457','4276dd15c15ac88519ece7e1a239eb79873f9977a2ad601b41ed800ae40045eb','474ae35a93a3d807be1f9581957d2b4a6e5aed6928ddb5f536334a4fa4485657','05ee424b11eaabd09ad3db46dee3e183d4b369f14a3b1fcb5da43b839e0c243b','3c1c2b4d2620ba138997734d080403bd175f973f4add6f1430f6af4d0f7182b4','abb388e20fc92eb1f5acabe5f45de4af0e8880f1a6d4bee267fbfc477208d01e'},
        'container_diagnostics.py':{'21cd245f1253eee696a7f173118d026f7d2f8002c9a90051be98f6d3f38eeddf','ae4a883cec4a7d061ca08e417ca963812f4499d2a7a3949f1226a5848dfcd6fb','1b2f7540db037f65dba08b8a31ed426fe465de45567785637e55389d5b51e7b5','23633ee9afdd400904ebf11406b8d3b441644b8f17bde48e842b01401166e297','e9ced481f94ef568a2f2116777124869aa5920757256d6936ac5da060c3eb192','ffb9d85621cec4de5ec46c79143545db85be736bcfa0adfb6756f316bf0f8fbe','af82e3af261924f3437ff9b6d049a236e48c63390b6776f9652f075f181fa637'},
        'readonly.sudoers':{'2331b5daeb4fcf641320d59fdfb65cfd6e32ffa7df41e81410ed2c1cd3013270','d5e17ce55fecc18b33191de71b001672a49d72edf236bce2e9e738b85ca71c3d','c27aa3af2dc72986f56ae3c5785e715687edaa4b4d7ba2e2ff050c092fac7251'}}
DESTINATIONS={'readonly.py':'/usr/local/lib/nexarm-runner/readonly.py',
              'readonly.sudoers':'/etc/sudoers.d/nexarm-readonly',
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
    if current!=desired and hashlib.sha256(current).hexdigest() not in LEGACY.get(name,set()):
        raise ValueError('Unknown/custom broker file; preserved')


def main():
    if os.geteuid()!=0 or len(sys.argv)!=2:raise SystemExit('Operator root upgrade only')
    stage=Path(sys.argv[1])
    enrollment=json.loads(sys.stdin.read().removeprefix("\ufeff"))
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', enrollment['repository']):
        raise ValueError('Invalid repository identity')
    config_path=Path('/etc/nexarm-runner/readonly.json')
    if config_path.is_symlink() or config_path.stat().st_uid!=0 or config_path.stat().st_mode&0o077:
        raise SystemExit('Unsafe existing broker config; preserved')
    config=json.loads(config_path.read_text(encoding="utf-8-sig"))
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
                    if backup.is_symlink() or backup.stat().st_uid!=0 or backup.stat().st_mode&0o077:
                        raise SystemExit('Unsafe existing diagnostic backup; preserved')
                    require_known_file(name,backup.read_bytes(),desired)
                else:
                    backups.append((backup,current))
        elif name in ('readonly.py','readonly.sudoers'):raise SystemExit('Expected existing broker/policy missing')
    # Grant commands only after validating all enrolled files and config.
    for backup,current in backups:
        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as output:output.write(current)
    config.update(diagnostics_enabled=True,github_repository=enrollment['repository'])
    atomic(config_path,json.dumps(config,indent=2).encode()+b'\n',0o600)
    for name in ['container_diagnostics.py','readonly.py','readonly.sudoers']:
        atomic(Path(DESTINATIONS[name]),(stage/name).read_bytes(),0o440 if name.endswith('sudoers') else 0o644)
    print('Enrolled only fixed ROS / Aurora diagnostics; container and registration preserved.')


if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError):raise SystemExit('Diagnostic upgrade refused; check enrollment or host files') from None
