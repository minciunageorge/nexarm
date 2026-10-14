import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pi_job', ROOT / 'tools/pi_runner_job.py')
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


class JobTests(unittest.TestCase):
    def test_suite_continues_and_retains_failed_stdout_and_stderr(self):
        commands = []
        def execute(command, **kwargs):
            commands.append(command)
            kwargs['stdout'].write(b'observation')
            kwargs['stderr'].write(b'diagnostic warning')
            return subprocess.CompletedProcess(command, 1 if command[-1] == 'ros-inspect' else 0)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'artifacts'
            with patch('sys.argv', ['job', 'diagnostics-suite', '--output-dir', str(output)]), patch.object(job.subprocess, 'run', side_effect=execute):
                with self.assertRaises(SystemExit) as result:
                    job.main()
            self.assertEqual(result.exception.code, 1)
            self.assertEqual(commands, [['/usr/bin/sudo', '-n', '/usr/local/sbin/nexarm-readonly', op] for op in job.SUITE])
            self.assertNotIn('aurora-recover', job.SUITE)
            self.assertEqual((output/'ros-inspect/stdout.txt').read_bytes(), b'observation')
            self.assertEqual((output/'ros-inspect/broker-error.log').read_bytes(), b'diagnostic warning')
            self.assertFalse((output/'ros-inspect/metadata.txt').exists())
            self.assertTrue((output/'camera-one-frame/results.tar.gz').exists())
            self.assertEqual(len(json.loads((output/'manifest.json').read_text())['results']), 7)

    def test_timeout_preserves_partial_binary_without_success_archive(self):
        def execute(command, **kwargs):
            kwargs['stdout'].write(b'partial archive')
            raise subprocess.TimeoutExpired(command, 1)
        with tempfile.TemporaryDirectory() as directory, patch.object(job.subprocess, 'run', side_effect=execute):
            record = job.capture('camera-one-frame', Path(directory), 1)
            self.assertEqual(record['exit_code'], 124)
            self.assertTrue(record['timed_out'])
            self.assertEqual((Path(directory)/'stdout.bin').read_bytes(), b'partial archive')
            self.assertFalse((Path(directory)/'results.tar.gz').exists())

    def test_unapproved_operation_never_invokes_subprocess(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(job.subprocess, 'run') as execute:
            with self.assertRaises(ValueError):
                job.capture('docker exec nexarm sh', Path(directory), 1)
            execute.assert_not_called()

    def test_stale_directory_is_refused_without_broker_call(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'metadata.txt').write_text('old success')
            with patch('sys.argv', ['job', 'probe', '--output-dir', directory]), patch.object(job.subprocess, 'run') as execute:
                with self.assertRaises(SystemExit):
                    job.main()
                execute.assert_not_called()
            self.assertEqual((Path(directory)/'metadata.txt').read_text(), 'old success')
