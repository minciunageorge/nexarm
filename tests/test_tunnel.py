import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('render_tunnel', ROOT / 'tools/render_tunnel.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class TunnelTests(unittest.TestCase):
    def test_ports_and_roles_are_consistent(self):
        files = renderer.render('relay.example.invalid', 443, 2444)
        self.assertIn('RemoteForward 127.0.0.1:2444 127.0.0.1:22', files['pi/relay.conf'])
        self.assertIn('IdentityAgent none', files['pi/relay.conf'])
        self.assertIn('ProxyJump nexarm-relay-runner', files['runner/ssh_config'])
        self.assertIn('PermitListen 127.0.0.1:2444', files['relay/60-nexarm-tunnel.conf'])
        self.assertIn('PermitOpen 127.0.0.1:2444', files['relay/60-nexarm-tunnel.conf'])
        self.assertIn('GatewayPorts no', files['relay/60-nexarm-tunnel.conf'])
        self.assertNotIn('PRIVATE KEY', ''.join(files.values()))

    def test_ssh_configs_parse_without_connecting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = renderer.render('relay.example.invalid', 443, 2444)
            for name, alias in [('pi/relay.conf', 'nexarm-relay'), ('runner/ssh_config', 'nexarm-robot')]:
                path = root / Path(name).name
                path.write_text(files[name])
                result = subprocess.run(['ssh', '-G', '-F', str(path), alias], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('stricthostkeychecking true', result.stdout)
                self.assertIn('batchmode yes', result.stdout)
                if alias == 'nexarm-robot':
                    self.assertIn('port 2444', result.stdout)
                    self.assertIn('proxyjump nexarm-relay-runner', result.stdout)

    def test_service_parses_and_only_starts_ssh(self):
        unit = ROOT / 'deploy/tunnel/nexarm-tunnel.service'
        result = subprocess.run(['systemd-analyze', 'verify', str(unit)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = unit.read_text()
        self.assertIn('Restart=always', text)
        self.assertIn('WantedBy=multi-user.target', text)
        self.assertNotIn('docker', text)
        self.assertNotIn('ros2', text)

    def test_rejects_config_injection_and_bad_ports(self):
        for host in ['relay\nProxyCommand bad', '-oBad', 'user@relay', 'https://relay']:
            with self.assertRaises(ValueError):
                renderer.render(host)
        for port in [0, 65536]:
            with self.assertRaises(ValueError):
                renderer.render('relay.example.invalid', relay_port=port)
        with self.assertRaises(ValueError):
            renderer.render('relay.example.invalid', reverse_port=22)

    def test_renderer_preserves_existing_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / 'keep'
            marker.write_text('existing')
            result = subprocess.run(['python3', str(ROOT / 'tools/render_tunnel.py'),
                                     '--relay-host', 'relay.example.invalid', '--output-dir', temporary],
                                    capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(marker.read_text(), 'existing')

    def test_cli_uses_dedicated_config(self):
        spec = importlib.util.spec_from_file_location('nexarm', ROOT / 'tools/nexarm.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        command = cli.ssh_command('nexarm-robot', 2222, None, 'probe', ssh_config='/tmp/runner.conf')
        self.assertIn('-F', command)
        self.assertEqual(command[command.index('-F') + 1], '/tmp/runner.conf')

    def test_ci_access_files_are_private_and_public_keys_are_validated(self):
        import sys
        sys.path.insert(0, str(ROOT / 'tools'))
        try:
            import prepare_actions_access as access
            bindings = dict(NEXARM_RELAY_HOST='relay.example.invalid',
                            NEXARM_RELAY_HOST_KEY='ssh-ed25519 AAAATEST',
                            NEXARM_PI_HOST_KEY='ssh-ed25519 AAAATEST',
                            NEXARM_RELAY_OPERATOR_KEY='synthetic-not-a-real-private-key',
                            NEXARM_PI_OPERATOR_KEY='synthetic-not-a-real-private-key')
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / 'ssh'
                access.prepare(directory, bindings)
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                for file in directory.iterdir():
                    self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                self.assertNotIn('synthetic-not-a-real-private-key', (directory / 'runner.conf').read_text())
                with self.assertRaises(FileExistsError):
                    access.prepare(directory, bindings)
            with self.assertRaises(ValueError):
                access.host_key('ssh-ed25519 AAAA\nevil.example ssh-ed25519 BBBB')
        finally:
            sys.path.remove(str(ROOT / 'tools'))

    def test_relay_effective_roles(self):
        import shutil
        sshd = shutil.which('sshd')
        if sshd is None:
            self.skipTest('sshd parser unavailable')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            host_key = root / 'host_key'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(host_key)], check=True)
            config = root / 'sshd_config'
            config.write_text(renderer.render('relay.example.invalid')['relay/60-nexarm-tunnel.conf'])
            for user, role, allowed, denied in [('nexarm-pi-tunnel', 'remote', 'permitlisten 127.0.0.1:2222', 'permitopen none'),
                                               ('nexarm-operator-tunnel', 'local', 'permitopen 127.0.0.1:2222', 'permitlisten none')]:
                result = subprocess.run([sshd, '-T', '-h', str(host_key), '-f', str(config), '-C',
                                         f'user={user},host=relay.example.invalid,addr=192.0.2.1'],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                for expected in [f'allowtcpforwarding {role}', allowed, denied, 'gatewayports no',
                                 'passwordauthentication no', 'maxsessions 0', 'allowstreamlocalforwarding no']:
                    self.assertIn(expected, result.stdout)
