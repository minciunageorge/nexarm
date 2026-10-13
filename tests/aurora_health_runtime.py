"""Synthetic fixed RGB/depth/IR delivery tests in an isolated disposable container."""
from pathlib import Path
import json,os,subprocess
script=(Path(__file__).resolve().parents[1]/'deploy/runner/container_diagnostics.py').read_bytes()
image=os.environ.get('NEXARM_DIAG_TEST_IMAGE','nexarm:diagnostics')
container=subprocess.check_output(['docker','run','-d','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp','--tmpfs','/results',image],text=True).strip()
base=['docker','exec','-i','--user','65534:65534','--env','HOME=/tmp','--env','ROS_LOG_DIR=/tmp','--env','LD_LIBRARY_PATH=/opt/ros/humble/lib','--env','AMENT_PREFIX_PATH=/opt/ros/humble',container,'/usr/bin/python3','-I','-']
def observe():
 result=subprocess.run(base+['aurora-health'],input=script,capture_output=True,timeout=25)
 assert result.returncode==0,result.stderr.decode()[:2000]
 return json.loads(result.stdout)
try:
 for deliver in [True,False]:
  source=f'''import sys,time
sys.path.insert(0,"/opt/ros/humble/local/lib/python3.10/dist-packages")
sys.path.insert(0,"/opt/ros/humble/lib/python3.10/site-packages")
import rclpy
from sensor_msgs.msg import Image
from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy
rclpy.init();n=rclpy.create_node("synthetic_aurora_health")
q=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.VOLATILE)
p={{c:n.create_publisher(Image,"/depth_cam/"+c+"/image_raw",q) for c in ["rgb","depth","ir"]}}
m=Image();m.width=1;m.height=1;m.step=3;m.encoding="rgb8";m.data=[1,2,3]
deadline=time.monotonic()+18;counter=0
while time.monotonic()<deadline:
 counter+=1
 if {deliver!r}:
  m.header.stamp.sec=counter;p["depth"].publish(m)
  m.header.stamp.sec=42;p["ir"].publish(m)
 rclpy.spin_once(n,timeout_sec=0.1)
n.destroy_node();rclpy.shutdown()
'''.encode()
  pub=subprocess.Popen(base,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  pub.stdin.write(source);pub.stdin.close()
  try:
   result=observe();streams=result['streams']
   assert all(s['publisher_count']==1 for s in streams.values()),result
   assert all(any(r['max_matched_publishers']==1 for r in s['readers']) for s in streams.values()),result
   if deliver:
    assert result['summary']=='rgb_silent_other_streams_delivering',result
    assert not streams['rgb']['messages_observed'],result
    assert streams['depth']['counter_advancing'] and streams['depth']['timestamp_advancing'],result
    assert streams['ir']['counter_advancing'] and not streams['ir']['timestamp_advancing'],result
    assert any(r['stamp_repeats']>0 for r in streams['ir']['readers']),result
    print('RGB silent; depth counters/stamps advance; IR counters advance with frozen stamps',flush=True)
   else:
    assert result['summary']=='all_observed_streams_silent',result
    assert all(s['status']=='matched_no_messages' for s in streams.values()),result
    print('All three publishers DDS-match while no message callbacks arrive',flush=True)
  finally:
   pub.wait(timeout=22)
   assert pub.returncode==0,pub.stderr.read().decode()[:2000]
 result=observe()
 assert all(s['status']=='no_publisher_discovered' for s in result['streams'].values()),result
 print('Absent publishers are reported separately from matched-but-silent streams',flush=True)
finally:
 subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,check=True)
