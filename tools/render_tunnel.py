#!/usr/bin/env python3
"""Render credential-free Pi, relay and Codex SSH configuration; installs nothing."""
import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def render(host, relay_port=22, reverse_port=2222):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', host):
        raise ValueError('Relay host must be a DNS hostname or IPv4 address')
    if not 1 <= relay_port <= 65535 or not 1024 <= reverse_port <= 65535:
        raise ValueError('Invalid port; reverse port must be unprivileged')
    pi = f'''Host nexarm-relay
    HostName {host}
    Port {relay_port}
    User nexarm-pi-tunnel
    IdentityFile /home/pi/.ssh/nexarm/relay_ed25519
    IdentitiesOnly yes
    IdentityAgent none
    UserKnownHostsFile /home/pi/.ssh/nexarm/relay_known_hosts
    HostKeyAlias nexarm-relay-infra
    StrictHostKeyChecking yes
    BatchMode yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    ExitOnForwardFailure yes
    ConnectTimeout 15
    ServerAliveInterval 15
    ServerAliveCountMax 3
    TCPKeepAlive yes
    ForwardAgent no
    RequestTTY no
    RemoteForward 127.0.0.1:{reverse_port} 127.0.0.1:22
'''
    runner = f'''Host nexarm-relay-runner
    HostName {host}
    Port {relay_port}
    User nexarm-operator-tunnel
    IdentityFile ~/.ssh/nexarm/operator_relay_ed25519
    IdentitiesOnly yes
    UserKnownHostsFile ~/.ssh/nexarm/relay_known_hosts
    HostKeyAlias nexarm-relay-infra
    StrictHostKeyChecking yes
    BatchMode yes
    ForwardAgent no
    ConnectTimeout 15
    ServerAliveInterval 15
    ServerAliveCountMax 3

Host nexarm-robot
    HostName 127.0.0.1
    Port {reverse_port}
    User pi
    ProxyJump nexarm-relay-runner
    IdentityFile ~/.ssh/nexarm/pi_operator_ed25519
    IdentitiesOnly yes
    UserKnownHostsFile ~/.ssh/nexarm/pi_known_hosts
    HostKeyAlias nexarm-pi5
    StrictHostKeyChecking yes
    BatchMode yes
    ForwardAgent no
    ConnectTimeout 15
    ServerAliveInterval 15
    ServerAliveCountMax 3
'''
    relay = f'''# Dedicated relay accounts only; validate effective policy before reload.
Match User nexarm-pi-tunnel
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding remote
    PermitListen 127.0.0.1:{reverse_port}
    PermitOpen none
    GatewayPorts no
    AllowAgentForwarding no
    AllowStreamLocalForwarding no
    PermitUserRC no
    MaxSessions 0
    X11Forwarding no
    PermitTTY no
    ForceCommand /bin/false

Match User nexarm-operator-tunnel
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:{reverse_port}
    PermitListen none
    GatewayPorts no
    AllowAgentForwarding no
    AllowStreamLocalForwarding no
    PermitUserRC no
    MaxSessions 0
    X11Forwarding no
    PermitTTY no
    ForceCommand /bin/false

Match all
'''
    return {'pi/relay.conf': pi,
            'pi/nexarm-tunnel.service': (ROOT / 'deploy/tunnel/nexarm-tunnel.service').read_text(),
            'runner/ssh_config': runner, 'relay/60-nexarm-tunnel.conf': relay,
            'relay/pi_authorized_key.options': f'restrict,port-forwarding,permitlisten="127.0.0.1:{reverse_port}"',
            'relay/operator_authorized_key.options': f'restrict,port-forwarding,permitopen="127.0.0.1:{reverse_port}"'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--relay-host', required=True)
    parser.add_argument('--relay-port', type=int, default=22)
    parser.add_argument('--reverse-port', type=int, default=2222)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        files = render(args.relay_host, args.relay_port, args.reverse_port)
    except ValueError as error:
        parser.error(str(error))
    # Refuse existing output to preserve operator files, even on partial reruns.
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        path = args.output_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + '\n')
    print(f'Rendered public configuration in {args.output_dir}; no credentials or services installed')


if __name__ == '__main__':
    main()
