import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a=sys.argv[1:]
with Path(os.environ['TRACE']).open('a') as f: f.write(json.dumps(a)+'\n')
if a[0]=='info': print('linux/aarch64')
elif a[0]=='inspect':
    if a[2]=='{{.Id}}': print('a'*64)
    elif a[2]=='{{.State.Running}}': print('true')
    else: print('id='+'a'*64+' running=true')
elif a[0]=='logs': print('existing logs')
elif a[0]=='cp':
    if a[1]=='nexarm:/missing': sys.exit(1)
    Path(a[2]).write_text('existing artifact')
else: raise SystemExit('Forbidden Docker operation: '+a[0])
'''


class BootstrapTests(unittest.TestCase):
    def prepare(self, root):
        for name, content in [('docker', DOCKER), ('uname', '#!/bin/sh\necho aarch64\n'),
                              ('id', '#!/bin/sh\necho pi\n')]:
            path = root / name
            path.write_text(content)
            path.chmod(0o755)
        return dict(os.environ, HOME=str(root), PATH=f'{root}:{os.environ["PATH"]}', TRACE=str(root / 'trace'))

    def test_bootstrap_is_repeatable_and_docker_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.prepare(root)
            for _ in range(2):
                result = subprocess.run(['bash', str(ROOT / 'deploy/bootstrap-preserve.sh')], env=env, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
            guard = root / '.local/share/nexarm/protect-existing'
            self.assertEqual(guard.read_text(), 'a'*64+'\n')
            self.assertEqual(guard.stat().st_mode & 0o777, 0o600)
            calls = [json.loads(line)[0] for line in (root / 'trace').read_text().splitlines()]
            self.assertTrue(set(calls) <= {'inspect', 'info'})

    def test_collect_existing_uses_only_logs_inspect_and_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.prepare(root)
            result = subprocess.run(['bash', str(ROOT / 'deploy/remote.sh'), 'collect-existing', '', 'no', 'none',
                                     '/root/.ros/log', '/saved camera;results'], env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            with tarfile.open(fileobj=io.BytesIO(result.stdout), mode='r:gz') as archive:
                self.assertIn('./container.log', archive.getnames())
                self.assertEqual(archive.extractfile('./path-2').read(), b'existing artifact')
            calls = [json.loads(line) for line in (root / 'trace').read_text().splitlines()]
            self.assertEqual([call[0] for call in calls], ['inspect', 'logs', 'cp', 'cp'])
            self.assertEqual(calls[-1][1], 'nexarm:/saved camera;results')

    def test_missing_requested_artifact_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.prepare(root)
            result = subprocess.run(['bash', str(ROOT / 'deploy/remote.sh'), 'collect-existing', '', 'no', 'none',
                                     '/missing'], env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b'')

    def test_discovery_uses_inspect_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self.prepare(root)
            result = subprocess.run(['bash', str(ROOT / 'deploy/remote.sh'), 'discover-existing', '', 'no', 'none'],
                                    env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            calls = [json.loads(line) for line in (root / 'trace').read_text().splitlines()]
            self.assertEqual([call[0] for call in calls], ['inspect', 'inspect'])
            self.assertNotIn('{{json .Config.Env}}', calls[0][2])
