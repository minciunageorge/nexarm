#!/usr/bin/env python3
"""One-time Pi activation; generated standalone copy embeds reviewed host files."""
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

PAYLOAD = {'deploy/bootstrap-preserve.sh': '#!/usr/bin/env bash\n# Run by pi via SSH: checks Docker read-only, writes only a host-side safety guard.\nset -euo pipefail\n[[ $(id -un) == pi ]] || { echo \'Run as pi, without sudo\' >&2; exit 1; }\n[[ $(uname -m) == aarch64 ]] || { echo \'64-bit ARM Linux required\' >&2; exit 1; }\nfor tool in docker tar flock; do command -v "$tool" >/dev/null; done\ndocker info --format \'Docker={{.OSType}}/{{.Architecture}}\'\n[[ $(docker inspect -f \'{{.State.Running}}\' nexarm) == true ]] || {\n  echo \'Expected running nexarm container; no changes made\' >&2; exit 1;\n}\ncontainer_id=$(docker inspect -f \'{{.Id}}\' nexarm)\n[[ $container_id =~ ^[0-9a-f]{64}$ ]] || { echo \'Invalid container ID\' >&2; exit 1; }\numask 077\nbase="$HOME/.local/share/nexarm"\nmkdir -p "$base"\n# Exclusive creation: repeat runs do not overwrite an existing guard.\nif [[ -e $base/protect-existing ]]; then\n  [[ $(cat "$base/protect-existing") == "$container_id" ]] || {\n    echo \'Guard references a different container; investigate manually\' >&2; exit 1;\n  }\nelse\n  (set -o noclobber; printf \'%s\\n\' "$container_id" >"$base/protect-existing")\nfi\nprintf \'Protected running nexarm: %s\\n\' "$container_id"\nprintf \'Guard: %s/protect-existing\\n\' "$base"\nprintf \'Container unchanged. Collection is enabled; deployment/camera execution remain blocked.\\n\'\n', 'deploy/install-pi-runner-host.sh': '#!/usr/bin/env bash\n# One-time HOST preparation only. No runner download, registration or activation.\nset -euo pipefail\n[[ $(id -u) == 0 ]] || { echo \'Run this reviewed host installer with sudo\' >&2; exit 1; }\nscript_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)\nfor tool in /usr/bin/python3 /usr/bin/docker visudo useradd sudo; do command -v "$tool" >/dev/null; done\nfor path in /etc/nexarm-runner /usr/local/lib/nexarm-runner /usr/local/sbin/nexarm-readonly /etc/sudoers.d/nexarm-readonly /etc/systemd/system/nexarm-actions.service; do\n  [[ ! -e $path && ! -L $path ]] || { echo "Preserving existing $path; inspect previous setup first" >&2; exit 1; }\ndone\nif id nexarm-runner >/dev/null 2>&1; then\n  echo \'Existing nexarm-runner account requires review; nothing overwritten\' >&2; exit 1\nfi\nvisudo -cf "$script_dir/runner/readonly.sudoers"\n# Only info/inspect against Docker, plus the existing pi-host preservation marker.\nsudo -H -u pi bash "$script_dir/bootstrap-preserve.sh"\ncontainer_id=$(cat /home/pi/.local/share/nexarm/protect-existing)\n[[ $container_id =~ ^[0-9a-f]{64}$ ]]\nuseradd --system --user-group --create-home --home-dir /var/lib/nexarm-runner --shell /usr/sbin/nologin nexarm-runner\nchmod 700 /var/lib/nexarm-runner\ninstall -d -o root -g root -m 755 /usr/local/lib/nexarm-runner\ninstall -d -o root -g root -m 700 /etc/nexarm-runner\ninstall -o root -g root -m 644 "$script_dir/runner/readonly.py" /usr/local/lib/nexarm-runner/readonly.py\ninstall -o root -g root -m 755 "$script_dir/runner/nexarm-readonly" /usr/local/sbin/nexarm-readonly\n# ID is a verified non-secret immutable Docker identity, not shell-generated code.\nprintf \'{"container_id":"%s","artifact_paths":[]}\\n\' "$container_id" >/etc/nexarm-runner/readonly.json\nchmod 600 /etc/nexarm-runner/readonly.json\ninstall -o root -g root -m 440 "$script_dir/runner/readonly.sudoers" /etc/sudoers.d/nexarm-readonly\ninstall -o root -g root -m 644 "$script_dir/runner/nexarm-actions.service" /etc/systemd/system/nexarm-actions.service\nprintf \'Host broker/account prepared; existing nexarm unchanged. Runner is NOT registered, enabled or started.\\n\'\n', 'deploy/runner/readonly.py': '#!/usr/bin/python3\n"""Root-installed, fixed-scope Docker read broker. Never import checkout code."""\nimport json\nimport os\nfrom pathlib import Path\nimport re\nimport resource\nimport subprocess\nimport sys\nimport tarfile\nimport tempfile\n\nCONFIG = Path(\'/etc/nexarm-runner/readonly.json\')\nDOCKER = \'/usr/bin/docker\'\nOPERATIONS = (\'probe\', \'discover-existing\', \'collect-existing\')\n\n\ndef validate(config):\n    identifier = config[\'container_id\']\n    if not re.fullmatch(r\'[0-9a-f]{64}\', identifier):\n        raise ValueError(\'Invalid enrolled container ID\')\n    paths = config.get(\'artifact_paths\', [])\n    if not isinstance(paths, list) or len(paths) > 8:\n        raise ValueError(\'Invalid artifact path list\')\n    for path in paths:\n        if not isinstance(path, str) or not path.startswith(\'/\') or path == \'/\' or \'..\' in Path(path).parts or \'\\n\' in path:\n            raise ValueError(\'Invalid artifact path\')\n        if Path(path).parts[1] in (\'dev\', \'proc\', \'sys\', \'etc\'):\n            raise ValueError(\'System paths are not result directories\')\n    return identifier, paths\n\n\ndef docker(arguments, output=None):\n    command = [DOCKER, \'--host\', \'unix:///var/run/docker.sock\', *arguments]\n    result = subprocess.run(command, stdout=output or subprocess.PIPE, stderr=subprocess.PIPE,\n                            timeout=120, env={\'PATH\': \'/usr/bin:/bin\', \'HOME\': \'/nonexistent\', \'LANG\': \'C.UTF-8\'})\n    if result.returncode:\n        raise RuntimeError(\'Docker read operation failed; confirm container and enrolled paths\')\n    return result.stdout\n\n\ndef execute(operation, config):\n    if operation not in OPERATIONS:\n        raise ValueError(\'Only read-only operations are enabled\')\n    identifier, paths = validate(config)\n    current = docker([\'inspect\', \'-f\', \'{{.Id}}\', \'nexarm\']).decode().strip()\n    if current != identifier:\n        raise ValueError(\'Container identity changed; administrator review required\')\n    if operation == \'probe\':\n        sys.stdout.buffer.write(docker([\'inspect\', \'-f\', \'id={{.Id}} running={{.State.Running}} image={{.Image}} started={{.State.StartedAt}}\', identifier]))\n    elif operation == \'discover-existing\':\n        template = \'user={{.Config.User}} workdir={{.Config.WorkingDir}}{{println}}{{range .Config.Env}}{{if ge (len .) 12}}{{if eq (slice . 0 12) "ROS_LOG_DIR="}}{{println .}}{{end}}{{end}}{{if ge (len .) 5}}{{if eq (slice . 0 5) "HOME="}}{{println .}}{{end}}{{end}}{{end}}\'\n        sys.stdout.buffer.write(docker([\'inspect\', \'-f\', template, identifier]))\n        sys.stdout.buffer.write(docker([\'inspect\', \'-f\', \'{{range .Mounts}}{{println .Type .Source .Destination}}{{end}}\', identifier]))\n    else:\n        with tempfile.TemporaryDirectory(prefix=\'nexarm-readonly-\') as temporary:\n            root = Path(temporary)\n            (root / \'status.txt\').write_bytes(docker([\'inspect\', \'-f\', \'id={{.Id}} running={{.State.Running}} image={{.Image}} started={{.State.StartedAt}}\', identifier]))\n            command = [DOCKER, \'--host\', \'unix:///var/run/docker.sock\', \'logs\', \'--timestamps\', \'--since\', \'24h\', \'--tail\', \'5000\', identifier]\n            with (root / \'container.log\').open(\'wb\') as logs:\n                subprocess.run(command, stdout=logs, stderr=subprocess.STDOUT, timeout=120, check=True,\n                               env={\'PATH\': \'/usr/bin:/bin\', \'HOME\': \'/nonexistent\', \'LANG\': \'C.UTF-8\'})\n            for index, path in enumerate(paths, 1):\n                docker([\'cp\', f\'{identifier}:{path}\', str(root / f\'path-{index}\')])\n            (root / \'paths.json\').write_text(json.dumps(paths, indent=2)+\'\\n\')\n            total = 0\n            for path in root.rglob(\'*\'):\n                if path.is_symlink() or not (path.is_file() or path.is_dir()):\n                    raise ValueError(\'Special files/symlinks not permitted in enrolled artifacts\')\n                if path.is_file():\n                    total += path.stat().st_size\n            if total > 64 * 1024 * 1024:\n                raise ValueError(\'Collection exceeds 64 MiB; narrow enrolled result paths\')\n            with tarfile.open(fileobj=sys.stdout.buffer, mode=\'w|gz\') as archive:\n                for path in sorted(root.iterdir()):\n                    archive.add(path, arcname=path.name, recursive=True)\n\n\ndef main():\n    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in OPERATIONS:\n        raise SystemExit(\'Usage: nexarm-readonly probe|discover-existing|collect-existing\')\n    # Installed config is root-owned; never honor caller environment/config paths.\n    info = CONFIG.stat()\n    if info.st_uid != 0 or info.st_mode & 0o022 or CONFIG.is_symlink():\n        raise SystemExit(\'Unsafe installed configuration ownership/permissions\')\n    try:\n        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))\n        execute(sys.argv[1], json.loads(CONFIG.read_text()))\n    except (KeyError, ValueError, RuntimeError, subprocess.SubprocessError):\n        raise SystemExit(\'Read-only collection failed; administrator inspection required\')\n\n\nif __name__ == \'__main__\':\n    main()\n', 'deploy/runner/nexarm-readonly': '#!/bin/sh\n# -I ignores caller PYTHONPATH/site packages; root-owned code only.\nexec /usr/bin/python3 -I /usr/local/lib/nexarm-runner/readonly.py "$@"\n', 'deploy/runner/readonly.sudoers': 'Defaults:nexarm-runner env_reset\nnexarm-runner ALL=(root) NOPASSWD: /usr/local/sbin/nexarm-readonly probe, /usr/local/sbin/nexarm-readonly discover-existing, /usr/local/sbin/nexarm-readonly collect-existing\n', 'deploy/runner/nexarm-actions.service': '[Unit]\nDescription=Nexarm GitHub ARM64 read-only runner (no deployment)\nWants=network-online.target\nAfter=network-online.target\nStartLimitIntervalSec=0\n\n[Service]\nUser=nexarm-runner\nGroup=nexarm-runner\nWorkingDirectory=/var/lib/nexarm-runner/actions-runner\nExecStart=/var/lib/nexarm-runner/actions-runner/run.sh\nRestart=always\nRestartSec=15\nKillMode=control-group\nTimeoutStopSec=60\nUMask=0077\nStandardInput=null\nProtectSystem=strict\nProtectHome=yes\nReadWritePaths=/var/lib/nexarm-runner\nPrivateTmp=yes\nPrivateDevices=yes\nProtectKernelTunables=yes\nProtectKernelModules=yes\nProtectControlGroups=yes\nRestrictSUIDSGID=yes\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n# sudo to the fixed read-only broker must remain possible; see the security guide.\nNoNewPrivileges=no\nTasksMax=128\nMemoryMax=512M\nCPUQuota=50%\n\n[Install]\nWantedBy=multi-user.target\n'}
ROOT = Path(__file__).resolve().parents[1]
FILES = ['deploy/bootstrap-preserve.sh', 'deploy/install-pi-runner-host.sh',
         'deploy/runner/readonly.py', 'deploy/runner/nexarm-readonly',
         'deploy/runner/readonly.sudoers', 'deploy/runner/nexarm-actions.service']


def release_metadata(data):
    version = data['tag_name'].removeprefix('v')
    if not re.fullmatch(r'2\.\d+\.\d+', version):
        raise ValueError('Unexpected runner version')
    name = f'actions-runner-linux-arm64-{version}.tar.gz'
    asset = next(a for a in data['assets'] if a['name'] == name)
    expected_url = f'https://github.com/actions/runner/releases/download/v{version}/{name}'
    if asset['browser_download_url'] != expected_url:
        raise ValueError('Unexpected official artifact URL')
    digest = asset.get('digest', '')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest or ''):
        raise ValueError('Official release lacks SHA256 digest; stop, do not bypass verification')
    return expected_url, digest.split(':')[1]


def verify_archive(path):
    # Reject archive traversal/link escapes before extracting as non-root runner.
    with tarfile.open(path) as archive:
        for member in archive:
            name = Path(member.name)
            if name.is_absolute() or '..' in name.parts or member.isdev() or member.isfifo():
                raise ValueError('Unsafe archive member')
            if member.issym() or member.islnk():
                target = Path(member.linkname)
                if target.is_absolute() or '..' in target.parts:
                    raise ValueError('Unsafe archive link')


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def main():
    if getpass.getuser() != 'pi' or os.geteuid() == 0 or platform.machine() != 'aarch64':
        raise SystemExit('Run as pi (not sudo) on 64-bit Raspberry Pi Linux')
    if os.environ.get('HOME') != '/home/pi':
        raise SystemExit('Expected HOME=/home/pi')
    for command in ['sudo', 'docker', 'systemctl', 'systemd-analyze', 'visudo', 'useradd', 'tar']:
        if shutil.which(command, path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin") is None:
            raise SystemExit(f'Missing prerequisite: {command}; nothing installed automatically')
    run(['sudo', '-v'])
    repository = input('PRIVATE GitHub repository [minciunageorge/nexarm]: ').strip() or 'minciunageorge/nexarm'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise SystemExit('Use owner/repository, not a URL')
    if input('Confirm private repository, trusted workflows and protected main; type PRIVATE: ').strip() != 'PRIVATE':
        raise SystemExit('No activation; private repository confirmation required')
    baseline = subprocess.check_output(['docker', 'inspect', '-f', '{{.Id}} {{.State.Running}} {{.State.StartedAt}}', 'nexarm'], text=True).strip()
    if not re.fullmatch(r'[0-9a-f]{64} true .+', baseline):
        raise SystemExit('Expected running nexarm; no container changes attempted')
    files = PAYLOAD or {name: (ROOT / name).read_text() for name in FILES}
    with tempfile.TemporaryDirectory(prefix='nexarm-activation-') as temporary:
        stage = Path(temporary)
        for name, contents in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents)
        # Root's installer runs the pi preservation helper; stage is pi-owned.
        installed = Path('/usr/local/sbin/nexarm-readonly').exists()
        if installed:
            destinations = {'deploy/runner/readonly.py': '/usr/local/lib/nexarm-runner/readonly.py',
                            'deploy/runner/nexarm-readonly': '/usr/local/sbin/nexarm-readonly',
                            'deploy/runner/readonly.sudoers': '/etc/sudoers.d/nexarm-readonly',
                            'deploy/runner/nexarm-actions.service': '/etc/systemd/system/nexarm-actions.service'}
            for source, target in destinations.items():
                run(['sudo', 'cmp', '-s', str(stage / source), target])
            run(['sudo', '-H', '-u', 'nexarm-runner', '/usr/bin/sudo', '-n', '/usr/local/sbin/nexarm-readonly', 'probe'])
        else:
            run(['sudo', 'bash', str(stage / 'deploy/install-pi-runner-host.sh')])
        result = subprocess.run(['systemctl', 'is-active', '--quiet', 'nexarm-actions.service'])
        if result.returncode == 0:
            print('Runner service already active; no restart or re-registration performed.')
        else:
            runner = '/var/lib/nexarm-runner/actions-runner'
            configured = subprocess.run(['sudo', 'test', '-f', runner+'/.runner']).returncode == 0
            if not configured:
                # No token needed for this public official-release metadata request.
                request = urllib.request.Request('https://api.github.com/repos/actions/runner/releases/latest',
                                                 headers={'Accept':'application/vnd.github+json','User-Agent':'nexarm-bootstrap'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    url, digest = release_metadata(json.load(response))
                archive = stage / 'runner.tar.gz'
                sha = hashlib.sha256()
                with urllib.request.urlopen(url, timeout=60) as response, archive.open('wb') as output:
                    while chunk := response.read(1024*1024):
                        sha.update(chunk); output.write(chunk)
                if sha.hexdigest() != digest:
                    raise SystemExit('SHA256 mismatch: runner will not be extracted or used')
                verify_archive(archive)
                # Never overwrite an existing partial runner directory silently.
                if subprocess.run(['sudo', 'test', '-e', runner]).returncode == 0:
                    raise SystemExit('Unconfigured runner directory exists; inspect it before retrying')
                run(['sudo', 'install', '-d', '-o', 'nexarm-runner', '-g', 'nexarm-runner', '-m', '700', runner])
                # Runner package is extracted as its own user, never as root.
                run(['sudo', 'install', '-o', 'nexarm-runner', '-g', 'nexarm-runner', '-m', '600', str(archive), runner+'/package.tar.gz'])
                run(['sudo', '-H', '-u', 'nexarm-runner', 'tar', '-xzf', runner+'/package.tar.gz', '-C', runner])
                run(['sudo', '-u', 'nexarm-runner', 'rm', runner+'/package.tar.gz'])
                print(f'In GitHub: {repository} → Settings → Actions → Runners → New self-hosted runner; select Linux ARM64.')
                token = getpass.getpass('Paste the SHORT-LIVED registration token here (hidden, never in chat): ')
                if not token.strip():
                    raise SystemExit('No registration token supplied; service stays stopped')
                # Token goes over stdin and into the runner-supported input environment,
                # not argv/history. sudo starts a shell with a fixed script only.
                registration = 'set -eu; cd /var/lib/nexarm-runner/actions-runner; IFS= read -r token; export ACTIONS_RUNNER_INPUT_TOKEN="$token"; unset token; exec ./config.sh --unattended --url "$1" --name nexarm-pi5-readonly --labels nexarm-readonly --work _work'
                run(['sudo', '-H', '-u', 'nexarm-runner', 'bash', '-c', registration, 'register', f'https://github.com/{repository}'],
                    input=token+'\n', text=True)
                token = None
            run(['sudo', 'systemd-analyze', 'verify', '/etc/systemd/system/nexarm-actions.service'])
            run(['sudo', 'systemctl', 'daemon-reload'])
            run(['sudo', 'systemctl', 'enable', '--now', 'nexarm-actions.service'])
        after = subprocess.check_output(['docker', 'inspect', '-f', '{{.Id}} {{.State.Running}} {{.State.StartedAt}}', 'nexarm'], text=True).strip()
        if after != baseline:
            raise SystemExit('Container state differs from baseline; investigate external changes. Bootstrap never modifies it.')
        print('Existing nexarm ID, running state and start time unchanged.')
        print('Activation requested. Confirm runner online in GitHub, then dispatch Pi harmless verification on main.')
        print('Service PID/active status alone is not a passed GitHub job. No deployment or motion enabled.')


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as error:
        raise SystemExit(f'Official GitHub download request failed (HTTP {error.code}); check Pi outbound access. Container untouched.') from None
    except ValueError as error:
        raise SystemExit(f'Activation stopped: {error}. No container modification attempted.') from None
    except subprocess.CalledProcessError as error:
        raise SystemExit(f'Activation step {Path(error.cmd[0]).name} failed (exit {error.returncode}); inspect its output. No container modification attempted.') from None
    except (KeyError, StopIteration, OSError, subprocess.SubprocessError, tarfile.TarError):
        # Never echo exception details that could include registration credentials.
        raise SystemExit('Activation stopped safely. Check connectivity/prerequisites or existing partial setup; container was not modified.') from None
