#!/usr/bin/env python3
"""One-time Pi activation; generated standalone copy embeds reviewed host files."""
import getpass
from collections import deque
from pathlib import PurePosixPath
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

PAYLOAD = None  # Filled only in the downloadable standalone build.
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
    """Resolve archive links with POSIX semantics, including linked ancestors.

    Symlink targets are relative to their parent; hardlink targets are relative
    to the archive root. Check both extraction-time and final graphs so forward
    links and later directory aliases cannot hide an escape.
    """
    with tarfile.open(path) as archive:
        members = archive.getmembers()
    nodes = {}

    def member_name(member):
        name = PurePosixPath(member.name)
        if name.is_absolute() or '..' in name.parts or '\x00' in member.name:
            raise ValueError('Unsafe archive member')
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise ValueError('Unsafe archive member type')
        return tuple(part for part in name.parts if part != '.')

    def resolve(parts, graph, follow_final_symlink=True):
        pending = deque(parts)
        resolved = []
        expansions = 0
        while pending:
            part = pending.popleft()
            if part in ('', '.'):
                continue
            if part == '..':
                if not resolved:
                    raise ValueError('Archive link resolves outside extraction root')
                resolved.pop()
                continue
            resolved.append(part)
            node = graph.get(tuple(resolved))
            if node and node.issym() and not pending and not follow_final_symlink:
                return tuple(resolved)
            if node and (node.issym() or node.islnk()):
                target = PurePosixPath(node.linkname)
                if target.is_absolute() or not node.linkname or '\x00' in node.linkname:
                    raise ValueError('Unsafe archive link')
                expansions += 1
                if expansions > len(graph) + 40:
                    raise ValueError('Cyclic or excessive archive link chain')
                # Hardlinks name an archive-root path, NOT a parent-relative path.
                resolved = resolved[:-1] if node.issym() else []
                pending.extendleft(reversed(target.parts))
            elif node and pending and not node.isdir():
                raise ValueError('Archive path has a non-directory ancestor')
        return tuple(resolved)

    for member in members:
        name = member_name(member)
        if not name and not member.isdir():
            raise ValueError('Unsafe archive root entry')
        parent = resolve(name[:-1], nodes)
        # Track physical destinations after resolving ancestor aliases. Leaving
        # these keyed by their textual archive names can hide a nested link.
        destination = parent + name[-1:]
        for length in range(1, len(parent) + 1):
            prefix = parent[:length]
            if prefix not in nodes:
                directory = tarfile.TarInfo('/'.join(prefix))
                directory.type = tarfile.DIRTYPE
                nodes[prefix] = directory
        previous = nodes.get(destination)
        if previous and not (previous.isdir() and member.isdir()):
            raise ValueError('Duplicate archive member can change link semantics')
        # Validate the destination using links already created by extraction.
        nodes[destination] = member
        if member.issym() or member.islnk():
            resolve(destination, nodes)

    for name, member in nodes.items():
        resolve(name, nodes)
        if member.issym():
            # Validate this header's target even if a later ancestor alias makes
            # its original name unreachable in the final graph.
            parent = resolve(name[:-1], nodes)
            resolve(parent + PurePosixPath(member.linkname).parts, nodes)
        if member.islnk():
            target = resolve(PurePosixPath(member.linkname).parts, nodes, follow_final_symlink=False)
            destination = nodes.get(target)
            if destination is None or not destination.isfile():
                raise ValueError('Archive hardlink must resolve to a regular file')


def ensure_host_setup(stage, container_id):
    """Reuse identical protected host setup, never reinstall or overwrite it."""
    installed = subprocess.run(['sudo', 'test', '-e', '/usr/local/sbin/nexarm-readonly']).returncode == 0
    if not installed:
        run(['sudo', 'bash', str(stage / 'deploy/install-pi-runner-host.sh')])
        return
    destinations = {'deploy/runner/readonly.py': '/usr/local/lib/nexarm-runner/readonly.py',
                    'deploy/runner/nexarm-readonly': '/usr/local/sbin/nexarm-readonly',
                    'deploy/runner/readonly.sudoers': '/etc/sudoers.d/nexarm-readonly',
                    'deploy/runner/nexarm-actions.service': '/etc/systemd/system/nexarm-actions.service'}
    for source, target in destinations.items():
        if subprocess.run(['sudo', 'test', '-L', target]).returncode == 0:
            raise ValueError('Installed host file is a symlink; review required')
        result = subprocess.run(['sudo', 'cmp', '-s', str(stage / source), target])
        if result.returncode:
            raise ValueError('Existing host setup differs; preserving it for administrator review')
        owner, mode = subprocess.check_output(['sudo', 'stat', '-c', '%u %a', target], text=True).split()
        if owner != '0' or int(mode, 8) & 0o022:
            raise ValueError('Installed host file has unsafe ownership or permissions')
    groups = subprocess.check_output(['id', '-Gn', 'nexarm-runner'], text=True).split()
    if set(groups) & {'root', 'sudo', 'docker'}:
        raise ValueError('Runner account has unexpected privileged groups')
    guard = Path('/home/pi/.local/share/nexarm/protect-existing').read_text().strip()
    if guard != container_id:
        raise ValueError('Preservation guard differs from live container; review required')
    # Installed broker also validates its root-owned config and enrolled identity.
    run(['sudo', '-H', '-u', 'nexarm-runner', '/usr/bin/sudo', '-n', '/usr/local/sbin/nexarm-readonly', 'probe'])
    print('Resuming: protected host setup already complete; no files/account/guard overwritten.')


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
        ensure_host_setup(stage, baseline.split()[0])
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
