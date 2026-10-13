#!/usr/bin/env python3
"""One-time Pi activation; generated standalone copy embeds reviewed host files."""
import getpass
from collections import deque
from pathlib import PurePosixPath
import hashlib
import json
import sys
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
         'deploy/runner/readonly.sudoers', 'deploy/runner/nexarm-actions.service',
         'deploy/runner/container_diagnostics.py',
         'deploy/runner/upgrade_diagnostics.py']


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


def assert_runner_idle():
    worker = subprocess.run(['pgrep', '-u', 'nexarm-runner', '-f', 'Runner[.]Worker'])
    if worker.returncode != 1:
        raise ValueError('Runner job is active or cannot be checked; retry when idle')


def runner_has_no_new_privileges():
    pid = int(subprocess.check_output(['systemctl', 'show', '--property=MainPID', '--value', 'nexarm-actions.service'], text=True).strip())
    if pid <= 0:
        raise ValueError('Running service has no main PID; retry after inspecting its status')
    status = Path(f'/proc/{pid}/status').read_text()
    flag = next(line for line in status.splitlines() if line.startswith('NoNewPrivs:'))
    return flag.split()[1] != '0'


def refresh_running_runner(unit_changed):
    """Apply an idle service upgrade; retry a previously deferred restart."""
    if not unit_changed and not runner_has_no_new_privileges():
        return False
    assert_runner_idle()
    run(['sudo', 'systemctl', 'daemon-reload'])
    run(['sudo', 'systemctl', 'restart', 'nexarm-actions.service'])
    if runner_has_no_new_privileges():
        raise ValueError('Live runner still has NoNewPrivs=1; inspect service overrides')
    print('Restarted only the idle runner service; live NoNewPrivs=0 confirmed.')
    return True


LEGACY_RUNNER_UNIT_SHA256 = "6b4ad04a5bf4a75bb6da45377c847518d22006a734751c1fc4d27b58002741a2"


def upgrade_runner_unit(source):
    """Replace only the known historical unit; preserve sudoers/broker/config."""
    unit = '/etc/systemd/system/nexarm-actions.service'
    if subprocess.run(['sudo', 'test', '-L', unit]).returncode == 0:
        raise ValueError('Runner unit is a symlink; preserving it')
    owner, mode = subprocess.check_output(['sudo', 'stat', '-c', '%u %a', unit], text=True).split()
    if owner != '0' or int(mode, 8) & 0o022:
        raise ValueError('Unsafe runner unit ownership/permissions; preserving it')
    existing = subprocess.check_output(['sudo', 'cat', unit])
    dropins = subprocess.check_output(['systemctl', 'show', '--property=DropInPaths', '--value', 'nexarm-actions.service'], text=True).strip()
    if dropins:
        raise ValueError('Runner service has local overrides; preserve and review them first')
    if existing == source.read_bytes():
        return False
    if hashlib.sha256(existing).hexdigest() != LEGACY_RUNNER_UNIT_SHA256:
        raise ValueError('Unknown/custom runner unit; preserving it for review')
    assert_runner_idle()
    run(['sudo', 'systemd-analyze', 'verify', str(source)])
    # Fixed destinations, atomic replacement, one protected backup, no arbitrary
    # files or root-command authorization granted to nexarm-runner.
    code = r'''import hashlib, os, pathlib, tempfile, sys
unit = pathlib.Path("/etc/systemd/system/nexarm-actions.service")
legacy = sys.argv[2]
current = unit.read_bytes()
if unit.is_symlink() or unit.stat().st_uid != 0 or unit.stat().st_mode & 0o022:
    raise SystemExit("Unsafe installed runner unit; preserved")
if hashlib.sha256(current).hexdigest() != legacy:
    raise SystemExit("Unit changed during upgrade; preserved")
backup = unit.with_name(unit.name + ".nexarm-v1.bak")
if os.path.lexists(backup):
    if backup.is_symlink() or backup.stat().st_uid != 0 or backup.stat().st_mode & 0o022 or backup.read_bytes() != current:
        raise SystemExit("Existing backup differs; preserved")
else:
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output: output.write(current)
new = pathlib.Path(sys.argv[1]).read_bytes()
fd, temporary = tempfile.mkstemp(prefix=".nexarm-unit-", dir=unit.parent)
try:
    with os.fdopen(fd, "wb") as output:
        output.write(new); output.flush(); os.fsync(output.fileno())
    os.chmod(temporary, 0o644)
    if unit.read_bytes() != current: raise SystemExit("Unit changed; preserved")
    os.replace(temporary, unit)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
'''
    run(['sudo', '/usr/bin/python3', '-I', '-c', code, str(source), LEGACY_RUNNER_UNIT_SHA256])
    print('Upgraded only the recognized runner unit; old unit backed up. Broker/sudoers/guard unchanged.')
    return True


def ensure_host_setup(stage, container_id, enrollment=None):
    """Reuse identical protected host setup, never reinstall or overwrite it."""
    installed = subprocess.run(['sudo', 'test', '-e', '/usr/local/sbin/nexarm-readonly']).returncode == 0
    if not installed:
        run(['sudo', 'bash', str(stage / 'deploy/install-pi-runner-host.sh')])
        if enrollment is not None:
            run(['sudo','visudo','-cf',str(stage/'deploy/runner/readonly.sudoers')])
            run(['sudo','/usr/bin/python3','-I',str(stage/'deploy/runner/upgrade_diagnostics.py'),str(stage/'deploy/runner')],
                input=json.dumps(dict(enrollment,container_id=container_id)),text=True)
        return False
    groups = subprocess.check_output(['id', '-Gn', 'nexarm-runner'], text=True).split()
    if set(groups) & {'root', 'sudo', 'docker'}:
        raise ValueError('Runner account has unexpected privileged groups')
    guard = Path('/home/pi/.local/share/nexarm/protect-existing').read_text().strip()
    if guard != container_id:
        raise ValueError('Preservation guard differs from live container; review required')
    if enrollment is not None:
        assert_runner_idle()
        run(['sudo','visudo','-cf',str(stage/'deploy/runner/readonly.sudoers')])
        run(['sudo','/usr/bin/python3','-I',str(stage/'deploy/runner/upgrade_diagnostics.py'),str(stage/'deploy/runner')],
            input=json.dumps(dict(enrollment,container_id=container_id)),text=True)
    destinations = {'deploy/runner/readonly.py': '/usr/local/lib/nexarm-runner/readonly.py',
                    'deploy/runner/nexarm-readonly': '/usr/local/sbin/nexarm-readonly',
                    'deploy/runner/readonly.sudoers': '/etc/sudoers.d/nexarm-readonly'}
    for source, target in destinations.items():
        if subprocess.run(['sudo', 'test', '-L', target]).returncode == 0:
            raise ValueError('Installed host file is a symlink; review required')
        result = subprocess.run(['sudo', 'cmp', '-s', str(stage / source), target])
        if result.returncode:
            raise ValueError('Existing host setup differs; preserving it for administrator review')
        owner, mode = subprocess.check_output(['sudo', 'stat', '-c', '%u %a', target], text=True).split()
        if owner != '0' or int(mode, 8) & 0o022:
            raise ValueError('Installed host file has unsafe ownership or permissions')
    # Installed broker also validates its root-owned config and enrolled identity.
    run(['sudo', '-H', '-u', 'nexarm-runner', '/usr/bin/sudo', '-n', '/usr/local/sbin/nexarm-readonly', 'probe'])
    changed = upgrade_runner_unit(stage / 'deploy/runner/nexarm-actions.service')
    print('Resuming: protected broker/account/guard preserved.')
    return changed


def runner_manifest(archive_path):
    """Expected extracted tree from the freshly checksum-verified official tar."""
    manifest = {}
    with tarfile.open(archive_path) as archive:
        for member in archive:
            name = str(PurePosixPath(member.name))
            if name == '.':
                continue
            if member.isfile() or member.islnk():
                with archive.extractfile(member) as contents:
                    digest = hashlib.sha256()
                    while chunk := contents.read(1024 * 1024):
                        digest.update(chunk)
                manifest[name] = ('file', digest.hexdigest())
            elif member.issym():
                manifest[name] = ('link', member.linkname)
            else:
                manifest[name] = ('dir', None)
            for parent in PurePosixPath(name).parents:
                if str(parent) != '.':
                    manifest.setdefault(str(parent), ('dir', None))
    return manifest


def recover_unconfigured_runner(manifest, runner_path='/var/lib/nexarm-runner/actions-runner'):
    """Non-root cleanup of an exact verified package plus failed-config residue."""
    import stat
    root = Path(runner_path)
    if root != Path('/var/lib/nexarm-runner/actions-runner'):
        raise ValueError('Recovery is restricted to the fixed runner extraction directory')
    if root.is_symlink() or root.resolve() != root or not root.is_dir() or os.path.ismount(root):
        raise ValueError('Unsafe runner directory; nothing removed')
    uid = os.geteuid()
    if uid == 0 or root.stat().st_uid != uid:
        raise ValueError('Recovery must run as the dedicated runner directory owner')
    # A configured runner is protected regardless of marker contents/type.
    def protected():
        return any(p.name.startswith(('.runner', '.credentials')) or p.name == '.service'
                   for p in root.iterdir())
    if protected():
        raise ValueError('Configured/credentialed runner found; nothing removed')
    if not {'.env', '.path', '_diag'} <= {p.name for p in root.iterdir()}:
        raise ValueError('Not the recognized failed-registration residue; nothing removed')
    # Detect bind mounts too: os.path.ismount alone misses same-filesystem binds.
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        mount = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), line.split()[4])
        if mount == str(root) or mount.startswith(str(root) + '/'):
            raise ValueError('Runner extraction contains a mount; nothing removed')
    # Service status is checked by the caller; refuse any other runner-user process
    # too, including an unfinished config.sh or independently started listener.
    for process in Path('/proc').iterdir():
        if process.name.isdigit() and int(process.name) != os.getpid():
            try:
                if process.stat().st_uid == uid:
                    raise ValueError('Runner account has another process; nothing removed')
            except FileNotFoundError:
                pass
    allowed = {'.env', '.path', '_diag'}
    for item in root.rglob('*'):
        relative = item.relative_to(root).as_posix()
        info = item.lstat()
        if info.st_uid != uid or os.path.ismount(item):
            raise ValueError('Unexpected ownership/mount in runner residue')
        expected = manifest.get(relative)
        if expected:
            kind, value = expected
            if kind == 'link':
                if not stat.S_ISLNK(info.st_mode) or os.readlink(item) != value:
                    raise ValueError('Runner package link differs; nothing removed')
            elif kind == 'dir':
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError('Runner package directory differs; nothing removed')
            else:
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Runner package file differs; nothing removed')
                digest = hashlib.sha256()
                with item.open('rb') as contents:
                    while chunk := contents.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != value:
                    raise ValueError('Runner package contents differ; nothing removed')
        elif relative in allowed:
            if not (stat.S_ISDIR(info.st_mode) if relative == '_diag' else stat.S_ISREG(info.st_mode)):
                raise ValueError('Unsafe failed-registration residue')
        elif relative.startswith('_diag/'):
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError('Unsafe diagnostic residue')
        else:
            raise ValueError('Unknown runner-directory contents; nothing removed')
    for relative in manifest:
        if not os.path.lexists(root / relative):
            raise ValueError('Runner package is incomplete; nothing removed')
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError('Platform lacks safe directory removal; nothing removed')
    parent_fd = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if protected():
            raise ValueError('Registration appeared during inspection; nothing removed')
        shutil.rmtree(root.name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    print('Removed only verified unconfigured runner extraction and failed-registration residue.')


def recover_runner_residue(archive, runner):
    """Pass only public package hashes to cleanup running as nexarm-runner."""
    import inspect
    state = subprocess.check_output(['systemctl', 'show', '--property=ActiveState', '--value', 'nexarm-actions.service'], text=True).strip()
    if state not in ('inactive', 'failed'):
        raise ValueError('Runner service is not stopped; residue preserved')
    manifest = runner_manifest(archive)
    # The helper cannot access the pi-owned staging directory. Supply code plus
    # hashes over stdin, not root privileges or a mutable installed host helper.
    code = 'import os, json, hashlib, shutil, re\nfrom pathlib import Path\n' + inspect.getsource(recover_unconfigured_runner)
    code += '\nrecover_unconfigured_runner(' + repr(manifest) + ')\n'
    run(['sudo', '-H', '-u', 'nexarm-runner', '/usr/bin/python3', '-I', '-'], input=code, text=True)


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def main():
    if getpass.getuser() != 'pi' or os.geteuid() == 0 or platform.machine() != 'aarch64':
        raise SystemExit('Run as pi (not sudo) on 64-bit Raspberry Pi Linux')
    if os.environ.get('HOME') != '/home/pi':
        raise SystemExit('Expected HOME=/home/pi')
    for command in ['sudo', 'docker', 'systemctl', 'systemd-analyze', 'visudo', 'useradd', 'tar', 'pgrep']:
        if shutil.which(command, path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin") is None:
            raise SystemExit(f'Missing prerequisite: {command}; nothing installed automatically')
    run(['sudo', '-v'])
    repository = input('GitHub repository [minciunageorge/nexarm]: ').strip() or 'minciunageorge/nexarm'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise SystemExit('Use owner/repository, not a URL')
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
        registered='/var/lib/nexarm-runner/actions-runner/.runner'
        if subprocess.run(['sudo','test','-f',registered]).returncode==0:
            check='import json,sys; from pathlib import Path; actual=json.loads(Path(sys.argv[1]).read_text()).get("gitHubUrl","").rstrip("/").lower(); assert actual==sys.argv[2].lower(), "Selected repository differs from preserved runner registration"'
            run(['sudo','-H','-u','nexarm-runner','/usr/bin/python3','-I','-c',check,registered,f'https://github.com/{repository}'])
        unit_changed = ensure_host_setup(stage, baseline.split()[0],
                                         {'repository':repository})
        result = subprocess.run(['systemctl', 'is-active', '--quiet', 'nexarm-actions.service'])
        if result.returncode == 0:
            if not refresh_running_runner(unit_changed):
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
                if subprocess.run(['sudo', 'test', '-e', runner]).returncode == 0:
                    recover_runner_residue(archive, runner)
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
