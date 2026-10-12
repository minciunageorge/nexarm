#!/usr/bin/env python3
"""Explicit SSH deployment over a direct connection or user-managed tunnel."""
import argparse
import datetime
from pathlib import Path
import re
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def ssh_command(host, port, identity, operation, revision='', replace=False, network='none'):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', host):
        raise ValueError('Host must be a hostname or IPv4 address, without user, scheme or options')
    if not 1 <= port <= 65535:
        raise ValueError('Invalid SSH port')
    command = ['ssh', '-T', '-p', str(port), '-o', 'BatchMode=yes', '-o',
               'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-o',
               'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3']
    if identity:
        command += ['-i', identity]
    remote = ['bash', '-c', (ROOT / 'deploy/remote.sh').read_text(), 'nexarm',
              operation, revision, 'yes' if replace else 'no', network]
    return command + [f'pi@{host}', shlex.join(remote)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['probe', 'deploy', 'rollback', 'collect', 'camera'])
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=22)
    parser.add_argument('--identity')
    parser.add_argument('--apply', action='store_true', help='Required for deploy/rollback/camera')
    parser.add_argument('--replace-managed', action='store_true')
    parser.add_argument('--ros-network', choices=['none', 'host'], default='none')
    parser.add_argument('--topic', help='Explicit sensor_msgs/msg/Image topic; camera only')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'artifacts')
    args = parser.parse_args()
    if args.operation == 'camera' and (not args.topic or not re.fullmatch(r'/[A-Za-z0-9_/]+', args.topic)):
        parser.error('camera requires an absolute --topic')
    revision = args.topic or ''
    if args.operation == 'deploy':
        if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT):
            parser.error('Commit changes first: deployment requires a clean working tree')
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    command = ssh_command(args.host, args.port, args.identity, args.operation, revision,
                          args.replace_managed, args.ros_network)
    if args.operation in ('deploy', 'rollback', 'camera') and not args.apply:
        print(f'Dry run: {args.operation} to pi@{args.host}:{args.port}; network={args.ros_network}; revision/topic={revision}')
        print('No remote command executed. Add --apply to proceed.')
        return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.operation == 'collect':
        archive = args.output_dir / f'{stamp}.tar.gz'
        with archive.open('xb') as output:
            result = subprocess.run(command, stdout=output)
        if result.returncode:
            archive.unlink()
        else:
            print(archive)
        raise SystemExit(result.returncode)
    payload = None
    if args.operation == 'deploy':
        payload = subprocess.check_output(['git', 'archive', '--format=tar', revision], cwd=ROOT)
    result = subprocess.run(command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (args.output_dir / f'{stamp}-{args.operation}.log').write_bytes(result.stdout)
    print(result.stdout.decode(errors='replace'), end='')
    if args.operation in ('deploy', 'rollback', 'camera'):
        collect = ssh_command(args.host, args.port, args.identity, 'collect')
        archive = args.output_dir / f'{stamp}-results.tar.gz'
        with archive.open('xb') as output:
            gathered = subprocess.run(collect, stdout=output)
        if gathered.returncode:
            archive.unlink()
            print('Artifact collection failed; operation log retained. Retry collect.')
        elif result.returncode == 0:
            print(f'Artifacts: {archive}')
        if result.returncode == 0 and gathered.returncode:
            raise SystemExit(gathered.returncode)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
