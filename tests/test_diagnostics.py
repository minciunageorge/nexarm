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
broker=load('readonly');gate=load('repository_gate')

class DiagnosticsTests(unittest.TestCase):
 def test_public_metadata_and_wrong_repository_fail(self):
  for metadata in [{'private':False,'full_name':'owner/repo'},{'private':True,'full_name':'other/repo'}]:
   response=MagicMock();response.__enter__.return_value=io.StringIO(json.dumps(metadata))
   with patch.object(gate.urllib.request,'urlopen',return_value=response):
    with self.assertRaises(ValueError):gate.repository_metadata('owner/repo','synthetic-token')

 def test_unenrolled_or_public_gate_prevents_any_docker_call(self):
  with patch.object(broker,'private_gate',side_effect=ValueError('private gate')),patch.object(broker,'docker') as docker:
   for operation in ['ros-inspect','camera-one-frame']:
    with self.assertRaises(ValueError):broker.execute(operation,{'container_id':'a'*64})
   docker.assert_not_called()

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

 def test_existing_public_workflows_cannot_allocate_pi(self):
  for name in ['pi-readonly.yml','pi-verify.yml','pi-diagnostics.yml']:
   workflow=(ROOT/'.github/workflows'/name).read_text()
   self.assertIn("if: github.event.repository.private == true && github.ref == 'refs/heads/main'",workflow)
   self.assertNotIn('pull_request',workflow)

 def test_privacy_is_required_even_for_old_broker_operations(self):
  with patch.object(broker,'private_gate',side_effect=ValueError('private gate')),patch.object(broker,'docker') as docker:
   for operation in ['probe','discover-existing','collect-existing']:
    with self.assertRaises(ValueError):broker.execute(operation,{'container_id':'a'*64})
   docker.assert_not_called()

 def test_broker_upgrade_recognizes_only_legacy_or_desired_files(self):
  upgrade=load('upgrade_diagnostics')
  for name in ['readonly.py','readonly.sudoers']:
   old=(ROOT/'tests/fixtures'/('diagnostics-legacy-'+name)).read_bytes()
   desired=(ROOT/'deploy/runner'/name).read_bytes()
   upgrade.require_known_file(name,old,desired)
   upgrade.require_known_file(name,desired,desired)
   with self.assertRaises(ValueError):upgrade.require_known_file(name,b'custom policy',desired)

 def test_public_repository_upgrade_never_writes(self):
  import sys,types
  upgrade=load('upgrade_diagnostics')
  fake=types.ModuleType('repository_gate');fake.repository_metadata=MagicMock(side_effect=ValueError('public'))
  with patch.object(upgrade.os,'geteuid',return_value=0),patch.object(upgrade.sys,'argv',['upgrade','/tmp/stage']),patch.object(upgrade.sys,'stdin',io.StringIO(json.dumps({'repository':'owner/repo','token':'synthetic'}))),patch.dict(sys.modules,{'repository_gate':fake}),patch.object(upgrade,'atomic') as atomic:
   with self.assertRaises(ValueError):upgrade.main()
   atomic.assert_not_called()

 def test_healthy_private_metadata_is_accepted(self):
  response=MagicMock();response.__enter__.return_value=io.StringIO(json.dumps({'private':True,'full_name':'owner/repo'}))
  with patch.object(gate.urllib.request,'urlopen',return_value=response):
   self.assertTrue(gate.repository_metadata('owner/repo','synthetic')['private'])
