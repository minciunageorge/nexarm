#!/usr/bin/env python3
"""Materialize CI-only SSH credentials from secure runner bindings, never logs them."""
import argparse
import os
from pathlib import Path
import re
from render_tunnel import render


def host_key(value):
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/]+={0,3}', value):
        raise ValueError('Host keys must be independently verified Ed25519 public key type + base64, without comments')
    return value


def prepare(directory, bindings):
    files = render(bindings['NEXARM_RELAY_HOST'], int(bindings.get('NEXARM_RELAY_PORT') or 22),
                   int(bindings.get('NEXARM_REVERSE_PORT') or 2222))
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(directory.resolve())):
        raise ValueError('Use a simple absolute credential directory without whitespace')
    if not bindings['NEXARM_RELAY_OPERATOR_KEY'].strip() or not bindings['NEXARM_PI_OPERATOR_KEY'].strip():
        raise ValueError('Missing private key bindings')
    contents = {
        'runner.conf': files['runner/ssh_config'].replace('~/.ssh/nexarm/', str(directory.resolve()) + '/'),
        'relay_known_hosts': 'nexarm-relay-infra ' + host_key(bindings['NEXARM_RELAY_HOST_KEY']) + '\n',
        'pi_known_hosts': 'nexarm-pi5 ' + host_key(bindings['NEXARM_PI_HOST_KEY']) + '\n',
        'operator_relay_ed25519': bindings['NEXARM_RELAY_OPERATOR_KEY'],
        'pi_operator_ed25519': bindings['NEXARM_PI_OPERATOR_KEY'],
    }
    # Refuse all pre-existing paths; this directory belongs to the ephemeral job.
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, content in contents.items():
        descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            output.write(content.rstrip() + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        prepare(args.ssh_dir, os.environ)
    except (KeyError, ValueError, FileExistsError):
        # Do not echo sensitive environment values, paths or exception strings.
        parser.error('Missing/invalid access bindings, or credential directory already exists')
    print('Ephemeral SSH access files prepared; no credentials printed')


if __name__ == '__main__':
    main()
