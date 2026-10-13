#!/usr/bin/env python3
"""Harmless verification: system metadata and existing read-only diagnostics."""
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import tarfile


def broker(operation, destination):
    with destination.open('wb') as output:
        subprocess.run(['/usr/bin/sudo', '-n', '/usr/local/sbin/nexarm-readonly', operation],
                       stdout=output, check=True, timeout=300)


def main():
    if os.geteuid() == 0:
        raise SystemExit('Verification must run as unprivileged runner')
    output = Path('artifacts'); output.mkdir(exist_ok=True)
    information = {'hostname':socket.gethostname(), 'os':platform.system(),
                   'kernel':platform.release(), 'architecture':platform.machine(),
                   'cpu_count':os.cpu_count(), 'uid':os.getuid(), 'groups':os.getgroups()}
    status = Path('/proc/self/status').read_text()
    flag = next(line for line in status.splitlines() if line.startswith('NoNewPrivs:'))
    information['no_new_privileges'] = int(flag.split()[1])
    if information['no_new_privileges'] != 0:
        raise SystemExit('Runner inherited NoNewPrivs=1; upgrade its systemd unit before using the approved broker')
    (output/'system.json').write_text(json.dumps(information,indent=2)+'\n')
    if information['architecture'] != 'aarch64':
        raise SystemExit('Expected native ARM64 runner')
    # Policy-list mode checks access WITHOUT executing these unapproved commands.
    for command in ['/usr/bin/id', '/bin/sh', '/usr/bin/docker']:
        check = subprocess.run(['/usr/bin/sudo', '-n', '-l', command],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if check.returncode == 0:
            raise SystemExit('Runner has unexpected sudo authorization outside the broker')
    # Connect only; no Docker API request, no robot access or motion command.
    with socket.socket(socket.AF_UNIX) as connection:
        try:
            connection.connect('/var/run/docker.sock')
        except PermissionError:
            pass
        else:
            raise SystemExit('Runner unexpectedly has direct Docker socket access')
    broker('probe',output/'container-before.txt')
    broker('discover-existing',output/'diagnostic-paths.txt')
    broker('collect-existing',output/'diagnostics.tar.gz')
    with tarfile.open(output/'diagnostics.tar.gz') as archive:
        names=set(archive.getnames())
        if not {'status.txt','container.log','paths.json'} <= names:
            raise SystemExit('Diagnostic archive missing expected records')
        paths=json.load(archive.extractfile('paths.json'))
        for index,_ in enumerate(paths,1):
            if not any(name==f'path-{index}' or name.startswith(f'path-{index}/') for name in names):
                raise SystemExit('Approved result path not present in archive')
    broker('probe',output/'container-after.txt')
    if (output/'container-before.txt').read_bytes() != (output/'container-after.txt').read_bytes():
        raise SystemExit('Container diagnostic state changed during verification')
    result={'docker_diagnostics':'passed', 'direct_docker_access':'denied', 'general_sudo_policy_checks':'denied',
            'container_state_comparison':'unchanged', 'approved_result_paths':paths,
            'ros_camera_file_checks':'passed for enrolled paths' if paths else 'not run: no paths approved yet',
            'deployment':'disabled', 'motion':'not executed'}
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(information,indent=2))
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
