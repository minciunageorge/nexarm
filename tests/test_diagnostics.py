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
   for operation in ['ros-inspect','camera-one-frame','aurora-health']:
    for enabled in [None,False,'true']:
     with self.assertRaises(ValueError):broker.execute(operation,{'container_id':'a'*64,'diagnostics_enabled':enabled})
   docker.assert_not_called()

 def test_enrolled_diagnostics_do_not_require_visibility_or_token(self):
  with patch.object(broker,'docker',return_value=b'a'*64),patch.object(broker,'observe') as observe:
   for operation in ['ros-inspect','camera-one-frame','aurora-health']:
    broker.execute(operation,{'container_id':'a'*64,'diagnostics_enabled':True})
   self.assertEqual(observe.call_count,3)

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
  for name in ['readonly.py','readonly.sudoers','container_diagnostics.py']:
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
    source=(ROOT/'tests/fixtures/diagnostics-legacy-container_diagnostics.py') if name=='container_diagnostics.py' else ROOT/'deploy/runner'/name
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

 def test_timeout_diagnostics_reach_error_artifact_without_raw_stderr(self):
  diagnostic={'topic':'/depth_cam/rgb/image_raw','publisher_count':1,'offered_reliability':['RELIABLE'],'offered_durability':['VOLATILE'],'attempts':[{'reliability':'RELIABLE','durability':'VOLATILE','matched_publishers':1,'max_matched_publishers':1,'messages_received':0}]}
  def execute(command,**kwargs):
   kwargs['stdout'].write(json.dumps({'camera_timeout':diagnostic,'untrusted':'never forwarded'}).encode())
   kwargs['stderr'].write(b'never forwarded raw stderr\n'*200+b'TimeoutError: No Aurora RGB frame')
   return subprocess.CompletedProcess(command,1)
  with patch.object(broker.subprocess,'run',side_effect=execute):
   with self.assertRaises(ValueError) as error:broker.observe('camera-one-frame','a'*64)
   message=str(error.exception)
   self.assertIn('"publisher_count": 1',message)
   self.assertIn('"max_matched_publishers": 1',message)
   self.assertIn('"messages_received": 0',message)
   self.assertNotIn('never forwarded',message)
  for field,value in [('topic','/other'),('publisher_count',True),('attempts',[]),('offered_reliability',['bad\ntext'])]:
   invalid=dict(diagnostic);invalid[field]=value
   with self.assertRaises(ValueError):broker.timeout_diagnostics(json.dumps({'camera_timeout':invalid}))

 def test_previous_qos_broker_and_observer_are_safe_upgrade_inputs(self):
  upgrade=load('upgrade_diagnostics')
  for name in ['readonly.py','container_diagnostics.py']:
   upgrade.require_known_file(name,(ROOT/'tests/fixtures'/('diagnostics-qos-'+name)).read_bytes(),(ROOT/'deploy/runner'/name).read_bytes())

 def test_previous_delivery_files_can_upgrade_to_health(self):
  upgrade=load('upgrade_diagnostics')
  for name in ['readonly.py','readonly.sudoers','container_diagnostics.py']:
   upgrade.require_known_file(name,(ROOT/'tests/fixtures'/('diagnostics-delivery-'+name)).read_bytes(),(ROOT/'deploy/runner'/name).read_bytes())

 def test_health_broker_rejects_unapproved_topics(self):
  def execute(command,**kwargs):
   self.assertEqual(command[-1],'aurora-health')
   kwargs['stdout'].write(json.dumps({'operation':'aurora-health','streams':{'rgb':{'topic':'/other'},'depth':{'topic':'/depth_cam/depth/image_raw'},'ir':{'topic':'/depth_cam/ir/image_raw'}}}).encode())
   return subprocess.CompletedProcess(command,0)
  with patch.object(broker.subprocess,'run',side_effect=execute):
   with self.assertRaisesRegex(ValueError,'Unexpected Aurora health topic'):broker.observe('aurora-health','a'*64)

 def test_health_is_narrowly_authorized_in_sudoers_and_workflow(self):
  policy=(ROOT/'deploy/runner/readonly.sudoers').read_text()
  self.assertIn('/usr/local/sbin/nexarm-readonly aurora-health',policy)
  self.assertNotIn('*',policy)
  self.assertNotIn('NOPASSWD: ALL',policy)
  workflow=(ROOT/'.github/workflows/pi-diagnostics.yml').read_text()
  self.assertIn('options: [ros-inspect, camera-one-frame, aurora-health, aurora-discover, aurora-recover]',workflow)
  self.assertIn("'.nexarm/aurora-trigger.txt'",workflow)
  self.assertNotIn('pull_request',workflow)

 def test_previous_health_files_can_upgrade_to_discovery(self):
  upgrade=load('upgrade_diagnostics')
  for name in ['readonly.py','readonly.sudoers','container_diagnostics.py']:
   upgrade.require_known_file(name,(ROOT/'tests/fixtures'/('diagnostics-health-'+name)).read_bytes(),(ROOT/'deploy/runner'/name).read_bytes())

 def test_discovery_rejects_claims_of_enabled_recovery(self):
  def execute(command,**kwargs):
   kwargs['stdout'].write(json.dumps({'operation':'aurora-recover','recovery':{'enabled':True,'status':'restarted'}}).encode())
   return subprocess.CompletedProcess(command,0)
  with patch.object(broker.subprocess,'run',side_effect=execute):
   with self.assertRaisesRegex(ValueError,'cannot enable camera recovery'):broker.observe('aurora-recover','a'*64)

 def test_discovery_preserves_real_stderr_exit_and_timeout(self):
  message=b'Traceback (most recent call last):\nPermissionError: [Errno 13] /dev/bus/usb/001\n'
  for timeout in [False,True]:
   def execute(command,**kwargs):
    kwargs['stderr'].write(message)
    if timeout:raise subprocess.TimeoutExpired(command,25)
    return subprocess.CompletedProcess(command,142)
   with patch.object(broker.subprocess,'run',side_effect=execute):
    with self.assertRaises(ValueError) as error:broker.observe('aurora-discover','a'*64)
   report=json.loads(str(error.exception))
   self.assertEqual(report['stderr'],message.decode())
   self.assertEqual(report['docker_client_timed_out'],timeout)
   self.assertEqual(report['exit_code'],None if timeout else 142)
   self.assertEqual(report['possible_signal'],None if timeout else 14)

 def test_discovery_stderr_is_bounded_with_traceback_tail_preserved(self):
  data=io.BytesIO(b'HEADER\n'+b'x'*100000+b'\nAttributeError: real cause at end\n')
  report=json.loads(broker.discovery_failure('aurora-discover',data,exit_code=1))
  self.assertTrue(report['stderr_truncated'])
  self.assertIn('HEADER',report['stderr'])
  self.assertIn('AttributeError: real cause at end',report['stderr'])
  self.assertLess(len(report['stderr']),66000)

 def test_previous_discovery_files_can_upgrade_to_error_reporting(self):
  upgrade=load('upgrade_diagnostics')
  for name in ['readonly.py','container_diagnostics.py']:
   upgrade.require_known_file(name,(ROOT/'tests/fixtures'/('diagnostics-discovery-'+name)).read_bytes(),(ROOT/'deploy/runner'/name).read_bytes())

 def test_job_failure_keeps_real_discovery_error_artifact(self):
  spec=importlib.util.spec_from_file_location('discovery_job',ROOT/'tools/pi_runner_job.py')
  job=importlib.util.module_from_spec(spec);spec.loader.exec_module(job)
  record={'operation':'aurora-discover','exit_code':1,'stderr':'PermissionError: USB directory denied'}
  def execute(command,**kwargs):
   self.assertEqual(command,['/usr/bin/sudo','-n','/usr/local/sbin/nexarm-readonly','aurora-discover'])
   kwargs['stderr'].write(json.dumps(record).encode())
   return subprocess.CompletedProcess(command,1)
  with tempfile.TemporaryDirectory() as directory:
   output=Path(directory)/'artifacts'
   with patch('sys.argv',['job','aurora-discover','--output-dir',str(output)]),patch.object(job.subprocess,'run',side_effect=execute):
    with self.assertRaises(SystemExit) as error:job.main()
   self.assertEqual(error.exception.code,1)
   self.assertEqual(json.loads((output/'broker-error.log').read_text()),record)
   self.assertFalse((output/'metadata.txt').exists())

 def test_previous_partial_discovery_observer_can_upgrade(self):
  upgrade=load('upgrade_diagnostics')
  upgrade.require_known_file('container_diagnostics.py',(ROOT/'tests/fixtures/diagnostics-partial-container_diagnostics.py').read_bytes(),(ROOT/'deploy/runner/container_diagnostics.py').read_bytes())

 def test_previous_large_graph_observer_can_upgrade_to_sdk_inventory(self):
  upgrade=load('upgrade_diagnostics')
  upgrade.require_known_file('container_diagnostics.py',(ROOT/'tests/fixtures/diagnostics-largegraph-container_diagnostics.py').read_bytes(),(ROOT/'deploy/runner/container_diagnostics.py').read_bytes())
