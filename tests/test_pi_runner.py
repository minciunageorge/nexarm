import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('readonly', ROOT / 'deploy/runner/readonly.py')
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)
IDENTIFIER = 'a'*64


class BrokerTests(unittest.TestCase):
    def test_non_read_operations_rejected_before_docker(self):
        with patch.object(broker, 'docker') as docker:
            for operation in ['deploy', 'camera', 'exec', 'stop', 'run', 'motion']:
                with self.assertRaises(ValueError):
                    broker.execute(operation, {'container_id': IDENTIFIER})
            docker.assert_not_called()

    def test_changed_container_fails(self):
        with patch.object(broker, 'docker', return_value=b'b'*64+b'\n') as docker:
            with self.assertRaises(ValueError):
                broker.execute('probe', {'container_id': IDENTIFIER})
            self.assertEqual(docker.call_count, 1)

    def test_metadata_only_uses_inspect(self):
        with patch.object(broker, 'docker', side_effect=[IDENTIFIER.encode()+b'\n', b'paths\n', b'mounts\n']) as docker:
            with patch.object(broker.sys, 'stdout') as output:
                broker.execute('discover-existing', {'container_id': IDENTIFIER})
            self.assertTrue(all(call.args[0][0] == 'inspect' for call in docker.call_args_list))

    def test_collection_uses_fixed_paths_and_no_exec(self):
        calls = []
        def docker(arguments, output=None):
            calls.append(arguments)
            if arguments[0]=='inspect': return IDENTIFIER.encode()+b'\n'
            if arguments[0]=='cp': Path(arguments[-1]).write_text('saved image result')
        def run(command, **kwargs):
            calls.append(command[3:])
            kwargs['stdout'].write(b'existing logs\n')
            return subprocess.CompletedProcess(command, 0)
        with patch.object(broker, 'docker', side_effect=docker), patch.object(broker.subprocess, 'run', side_effect=run):
            with patch.object(broker.sys, 'stdout') as output:
                output.buffer = io.BytesIO()
                broker.execute('collect-existing', {'container_id': IDENTIFIER, 'artifact_paths':['/results/camera']})
                import tarfile
                output.buffer.seek(0)
                with tarfile.open(fileobj=output.buffer,mode='r:gz') as archive:
                    self.assertEqual(archive.extractfile('path-1').read(),b'saved image result')
        self.assertTrue(all(call[0] in ('inspect','logs','cp') for call in calls))
        self.assertIn(['cp', IDENTIFIER+':/results/camera', calls[-1][-1]], calls)

    def test_system_paths_and_traversal_rejected(self):
        for path in ['/', '/etc/shadow', '/dev/video0', '/proc/1', '/sys/a', '/logs/../secret', 'relative']:
            with self.assertRaises(ValueError):
                broker.validate({'container_id':IDENTIFIER,'artifact_paths':[path]})

    def test_sudo_parser(self):
        import shutil
        if shutil.which('visudo') is None:
            self.skipTest('visudo is absent locally; installer requires it on Pi')
        result = subprocess.run(['visudo','-cf',str(ROOT/'deploy/runner/readonly.sudoers')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_runner_service_directives_parse_without_activation(self):
        # Verify directives with a harmless existing executable; real runner path
        # is deliberately absent locally and must be verified after installation.
        unit = (ROOT / 'deploy/runner/nexarm-actions.service').read_text()
        unit = unit.replace('/var/lib/nexarm-runner/actions-runner/run.sh', '/usr/bin/true')
        unit = unit.replace('/var/lib/nexarm-runner/actions-runner', '/tmp')
        unit = unit.replace('ReadWritePaths=/var/lib/nexarm-runner', 'ReadWritePaths=/tmp')
        unit = unit.replace('/usr/bin/sudo', '/usr/bin/true')  # executable absent here; parse only
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nexarm-actions.service'
            path.write_text(unit)
            result = subprocess.run(['systemd-analyze', 'verify', str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
