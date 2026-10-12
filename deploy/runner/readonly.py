#!/usr/bin/python3
"""Root-installed, fixed-scope Docker read broker. Never import checkout code."""
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import tarfile
import tempfile

CONFIG = Path('/etc/nexarm-runner/readonly.json')
DOCKER = '/usr/bin/docker'
OPERATIONS = ('probe', 'discover-existing', 'collect-existing')


def validate(config):
    identifier = config['container_id']
    if not re.fullmatch(r'[0-9a-f]{64}', identifier):
        raise ValueError('Invalid enrolled container ID')
    paths = config.get('artifact_paths', [])
    if not isinstance(paths, list) or len(paths) > 8:
        raise ValueError('Invalid artifact path list')
    for path in paths:
        if not isinstance(path, str) or not path.startswith('/') or path == '/' or '..' in Path(path).parts or '\n' in path:
            raise ValueError('Invalid artifact path')
        if Path(path).parts[1] in ('dev', 'proc', 'sys', 'etc'):
            raise ValueError('System paths are not result directories')
    return identifier, paths


def docker(arguments, output=None):
    command = [DOCKER, '--host', 'unix:///var/run/docker.sock', *arguments]
    result = subprocess.run(command, stdout=output or subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=120, env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8'})
    if result.returncode:
        raise RuntimeError('Docker read operation failed; confirm container and enrolled paths')
    return result.stdout


def execute(operation, config):
    if operation not in OPERATIONS:
        raise ValueError('Only read-only operations are enabled')
    identifier, paths = validate(config)
    current = docker(['inspect', '-f', '{{.Id}}', 'nexarm']).decode().strip()
    if current != identifier:
        raise ValueError('Container identity changed; administrator review required')
    if operation == 'probe':
        sys.stdout.buffer.write(docker(['inspect', '-f', 'id={{.Id}} running={{.State.Running}} image={{.Image}}', identifier]))
    elif operation == 'discover-existing':
        template = 'user={{.Config.User}} workdir={{.Config.WorkingDir}}{{println}}{{range .Config.Env}}{{if ge (len .) 12}}{{if eq (slice . 0 12) "ROS_LOG_DIR="}}{{println .}}{{end}}{{end}}{{if ge (len .) 5}}{{if eq (slice . 0 5) "HOME="}}{{println .}}{{end}}{{end}}{{end}}'
        sys.stdout.buffer.write(docker(['inspect', '-f', template, identifier]))
        sys.stdout.buffer.write(docker(['inspect', '-f', '{{range .Mounts}}{{println .Type .Source .Destination}}{{end}}', identifier]))
    else:
        with tempfile.TemporaryDirectory(prefix='nexarm-readonly-') as temporary:
            root = Path(temporary)
            (root / 'status.txt').write_bytes(docker(['inspect', '-f', 'id={{.Id}} running={{.State.Running}} image={{.Image}}', identifier]))
            command = [DOCKER, '--host', 'unix:///var/run/docker.sock', 'logs', '--timestamps', '--since', '24h', '--tail', '5000', identifier]
            with (root / 'container.log').open('wb') as logs:
                subprocess.run(command, stdout=logs, stderr=subprocess.STDOUT, timeout=120, check=True,
                               env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8'})
            for index, path in enumerate(paths, 1):
                docker(['cp', f'{identifier}:{path}', str(root / f'path-{index}')])
            (root / 'paths.json').write_text(json.dumps(paths, indent=2)+'\n')
            total = 0
            for path in root.rglob('*'):
                if path.is_symlink() or not (path.is_file() or path.is_dir()):
                    raise ValueError('Special files/symlinks not permitted in enrolled artifacts')
                if path.is_file():
                    total += path.stat().st_size
            if total > 64 * 1024 * 1024:
                raise ValueError('Collection exceeds 64 MiB; narrow enrolled result paths')
            with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
                for path in sorted(root.iterdir()):
                    archive.add(path, arcname=path.name, recursive=True)


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in OPERATIONS:
        raise SystemExit('Usage: nexarm-readonly probe|discover-existing|collect-existing')
    # Installed config is root-owned; never honor caller environment/config paths.
    info = CONFIG.stat()
    if info.st_uid != 0 or info.st_mode & 0o022 or CONFIG.is_symlink():
        raise SystemExit('Unsafe installed configuration ownership/permissions')
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
        execute(sys.argv[1], json.loads(CONFIG.read_text()))
    except (KeyError, ValueError, RuntimeError, subprocess.SubprocessError):
        raise SystemExit('Read-only collection failed; administrator inspection required')


if __name__ == '__main__':
    main()
