"""Run the real remote transaction script against a fake Docker daemon."""
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p=Path(os.environ['FAKE_STATE'])
s=json.loads(p.read_text())
a=sys.argv[1:]; op=a[0]
def save(): p.write_text(json.dumps(s))
if op=='info': print('linux/aarch64')
elif op=='inspect':
    name=a[-1]
    if name not in s: sys.exit(1)
    if '-f' in a:
        fmt=a[a.index('-f')+1]
        if 'managed' in fmt: print('true' if s[name]['managed'] else 'false')
        elif 'Running' in fmt: print('true' if s[name]['running'] else 'false')
        elif '.Image' in fmt: print(s[name]['image'])
elif op=='build':
    if os.environ.get('FAIL_BUILD'): sys.exit(1)
elif op=='rename': s[a[2]]=s.pop(a[1]); save()
elif op=='stop': s[a[-1]]['running']=False; save()
elif op=='start': s[a[-1]]['running']=True; save()
elif op=='run': s['nexarm']={'managed':True,'running':True,'image':a[-1]}; save()
elif op=='rm': s.pop(a[-1],None); save()
elif op=='exec':
    if os.environ.get('FAIL_SMOKE'): sys.exit(1)
else: sys.exit(2)
'''


class DeploymentTests(unittest.TestCase):
    def execute(self, managed=True, fail_build=False, fail_smoke=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / 'state.json'
            original = {'managed': managed, 'running': True, 'image': 'sha256:old', 'config': 'original'}
            state.write_text(json.dumps({'nexarm': original}))
            docker = root / 'docker'; docker.write_text(FAKE_DOCKER); docker.chmod(0o755)
            uname = root / 'uname'; uname.write_text('#!/bin/sh\necho aarch64\n'); uname.chmod(0o755)
            env = dict(os.environ, HOME=str(root), PATH=f'{root}:{os.environ["PATH"]}', FAKE_STATE=str(state))
            if fail_build: env['FAIL_BUILD'] = '1'
            if fail_smoke: env['FAIL_SMOKE'] = '1'
            payload = io.BytesIO()
            with tarfile.open(fileobj=payload, mode='w'): pass
            result = subprocess.run(['bash', str(ROOT / 'deploy/remote.sh'), 'deploy', 'a'*40, 'yes', 'none'],
                                    input=payload.getvalue(), env=env, capture_output=True)
            final = json.loads(state.read_text())
            previous = root / '.local/share/nexarm/previous-image'
            return result, final, original, previous.read_text() if previous.exists() else None

    def test_refuses_unmanaged_container(self):
        result, final, old, previous = self.execute(managed=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(final, {'nexarm': old})
        self.assertIsNone(previous)

    def test_build_failure_preserves_live_container(self):
        result, final, old, previous = self.execute(fail_build=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(final, {'nexarm': old})

    def test_smoke_failure_restores_exact_container(self):
        result, final, old, previous = self.execute(fail_smoke=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(final, {'nexarm': old})
        self.assertIsNone(previous)

    def test_success_records_previous_image(self):
        result, final, old, previous = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(set(final), {'nexarm'})
        self.assertEqual(final['nexarm']['image'], 'nexarm:'+'a'*40)
        self.assertEqual(previous, 'sha256:old\n')
