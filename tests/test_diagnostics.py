import base64
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch,MagicMock

ROOT=Path(__file__).resolve().parents[1]
def load(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'deploy/runner'/f'{name}.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
broker=load('readonly')

class DiagnosticsTests(unittest.TestCase):
 def test_unenrolled_diagnostics_prevent_any_docker_call(self):
  with patch.object(broker,'docker') as docker:
   for operation in ['ros-inspect','camera-one-frame']:
    for enabled in [None,False,'true']:
     with self.assertRaises(ValueError):broker.execute(operation,{'container_id':'a'*64,'diagnostics_enabled':enabled})
   docker.assert_not_called()

 def test_enrolled_diagnostics_do_not_require_visibility_or_token(self):
  with patch.object(broker,'docker',return_value=b'a'*64),patch.object(broker,'observe') as observe:
   for operation in ['ros-inspect','camera-one-frame']:
    broker.execute(operation,{'container_id':'a'*64,'diagnostics_enabled':True})
   self.assertEqual(observe.call_count,2)

 def test_camera_exec_is_fixed_nonroot_and_output_is_validated(self):
  pixels=b'\x01\x02\x03'*(640*400)
  result={'topic':'/depth_cam/rgb/image_raw','width':640,'height':400,'encoding':'rgb8','pixels':base64.b64encode(pixels).decode()}
  def execute(command,**kwargs):
   self.assertIn('65534:65534',command)
   self.assertEqual(command[-4:],['/usr/bin/python3','-I','-','camera-one-frame'])
   self.assertNotIn('bash',command);self.assertNotIn('sh',command)
   self.assertIn(b'signal.alarm(22)',kwargs['input'])
   kwargs['stdout'].write(json.dumps(result).encode())
   return subprocess.CompletedProcess(command,0)
  with patch.object(broker.subprocess,'run',side_effect=execute),patch.object(broker.sys,'stdout') as output:
   output.buffer=io.BytesIO();broker.observe('camera-one-frame','a'*64);output.buffer.seek(0)
   import tarfile
   with tarfile.open(fileobj=output.buffer,mode='r:gz') as archive:
    self.assertEqual(archive.extractfile('aurora-rgb.ppm').read(),b'P6\n640 400\n255\n'+pixels)

 def test_malformed_camera_is_not_exported(self):
  def execute(command,**kwargs):
   kwargs['stdout'].write(json.dumps({'topic':'/other','width':640,'height':400,'encoding':'rgb8','pixels':''}).encode())
   return subprocess.CompletedProcess(command,0)
  with patch.object(broker.subprocess,'run',side_effect=execute),patch.object(broker.sys,'stdout') as output:
   with self.assertRaises(ValueError):broker.observe('camera-one-frame','a'*64)
   output.buffer.write.assert_not_called()

 def test_public_workflows_remain_manual_main_only(self):
  for name in ['pi-readonly.yml','pi-verify.yml','pi-diagnostics.yml']:
   workflow=(ROOT/'.github/workflows'/name).read_text()
   self.assertIn("if: github.ref == 'refs/heads/main'",workflow)
   self.assertNotIn('pull_request',workflow)
   self.assertIn('workflow_dispatch',workflow)
   self.assertNotIn('repository.private',workflow)

 def test_probe_without_diagnostics_enrollment_uses_fixed_docker_read(self):
  with patch.object(broker,'docker',return_value=b'a'*64) as docker,patch.object(broker.sys,'stdout'):
   broker.execute('probe',{'container_id':'a'*64})
   self.assertEqual(docker.call_count,2)

 def test_broker_upgrade_recognizes_only_legacy_or_desired_files(self):
  upgrade=load('upgrade_diagnostics')
  for name in ['readonly.py','readonly.sudoers']:
   old=(ROOT/'tests/fixtures'/('diagnostics-legacy-'+name)).read_bytes()
   desired=(ROOT/'deploy/runner'/name).read_bytes()
   upgrade.require_known_file(name,old,desired)
   upgrade.require_known_file(name,desired,desired)
   with self.assertRaises(ValueError):upgrade.require_known_file(name,b'custom policy',desired)

 def test_upgrade_accepts_previously_gated_broker(self):
  upgrade=load('upgrade_diagnostics')
  upgrade.require_known_file('readonly.py',(ROOT/'tests/fixtures/diagnostics-gated-readonly.py').read_bytes(),(ROOT/'deploy/runner/readonly.py').read_bytes())

 def test_activation_requires_no_privacy_credential(self):
  for name in ['tools/activate_pi_runner.py','deploy/runner/upgrade_diagnostics.py','deploy/runner/readonly.py']:
   source=(ROOT/name).read_text()
   for removed in ['require_private','repository_metadata','github-metadata.token','type PRIVATE']:
    self.assertNotIn(removed,source)

 def test_gated_upgrade_and_retry_preserve_historical_backup_without_token(self):
  import os
  upgrade=load('upgrade_diagnostics')
  with tempfile.TemporaryDirectory() as temporary:
   host=Path(temporary)
   def mapped(value):
    value=str(value)
    return host/value.lstrip('/') if value.startswith(('/etc/','/usr/')) else Path(value)
   config=mapped('/etc/nexarm-runner/readonly.json');config.parent.mkdir(parents=True)
   config.write_text('\ufeff'+json.dumps({'container_id':'a'*64,'artifact_paths':['/results']}));config.chmod(0o600)
   destinations={name:str(mapped(path)) for name,path in upgrade.DESTINATIONS.items()}
   for name,destination in destinations.items():
    path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
    source=ROOT/'deploy/runner'/name
    path.write_bytes((ROOT/'tests/fixtures/diagnostics-gated-readonly.py').read_bytes() if name=='readonly.py' else source.read_bytes())
    path.chmod(0o644)
   backup=Path(destinations['readonly.py']+'.pre-diagnostics.bak')
   old=(ROOT/'tests/fixtures/diagnostics-legacy-readonly.py').read_bytes()
   backup.write_bytes(old);backup.chmod(0o600)
   token=config.with_name('github-metadata.token');token.write_text('unused-preserve');token.chmod(0o600)
   actual_stat=Path.stat
   def root_stat(path,**kwargs):
    info=list(actual_stat(path,**kwargs));info[4]=0
    return os.stat_result(info)
   for retry in range(2):
    with patch.object(upgrade,'Path',side_effect=mapped),patch.object(upgrade,'DESTINATIONS',destinations),patch.object(upgrade.os,'geteuid',return_value=0),patch.object(Path,'stat',root_stat),patch.object(upgrade.sys,'argv',['upgrade',str(ROOT/'deploy/runner')]),patch.object(upgrade.sys,'stdin',io.StringIO('\ufeff'+json.dumps({'repository':'minciunageorge/nexarm','container_id':'a'*64}))):
     upgrade.main()
   self.assertEqual(backup.read_bytes(),old)
   self.assertEqual(token.read_text(),'unused-preserve')
   self.assertEqual(json.loads(config.read_text())['artifact_paths'],['/results'])
   self.assertTrue(json.loads(config.read_text())['diagnostics_enabled'])
   self.assertEqual(Path(destinations['readonly.py']).read_bytes(),(ROOT/'deploy/runner/readonly.py').read_bytes())
