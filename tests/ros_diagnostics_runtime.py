"""Real Humble QoS checks in an isolated disposable synthetic container only."""
from pathlib import Path
import json,subprocess,time,base64,os
script=(Path(__file__).resolve().parents[1]/'deploy/runner/container_diagnostics.py').read_bytes()
image=os.environ.get('NEXARM_DIAG_TEST_IMAGE','nexarm:diagnostics')
container=subprocess.check_output(['docker','run','-d','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp','--tmpfs','/results',image],text=True).strip()
base=['docker','exec','-i','--user','65534:65534','--env','HOME=/tmp','--env','ROS_LOG_DIR=/tmp','--env','LD_LIBRARY_PATH=/opt/ros/humble/lib','--env','AMENT_PREFIX_PATH=/opt/ros/humble',container,'/usr/bin/python3','-I','-']
def observe(operation):
 result=subprocess.run(base+[operation],input=script,capture_output=True,timeout=25)
 assert result.returncode==0,result.stderr.decode()[:2000]
 return json.loads(result.stdout)
try:
 graph=observe('ros-inspect')
 assert graph['camera']['publisher_count']==0 and graph['camera']['publishers']==[]
 print('Graph reports no RGB publisher before the synthetic test',flush=True)
 # Separate synthetic publishers in the SAME isolated disposable container.
 # Reliable primary / best-effort fallback must receive from all four writers.
 for reliability in ['BEST_EFFORT','RELIABLE']:
  for durability in ['VOLATILE','TRANSIENT_LOCAL']:
   publisher=f'''import sys,time
sys.path.insert(0,"/opt/ros/humble/local/lib/python3.10/dist-packages")
sys.path.insert(0,"/opt/ros/humble/lib/python3.10/site-packages")
import rclpy
from sensor_msgs.msg import Image
from rclpy.qos import QoSProfile,ReliabilityPolicy,DurabilityPolicy,HistoryPolicy
rclpy.init();n=rclpy.create_node("synthetic_aurora")
qos=QoSProfile(history=HistoryPolicy.KEEP_LAST,depth=5,reliability=ReliabilityPolicy.{reliability},durability=DurabilityPolicy.{durability})
p=n.create_publisher(Image,"/depth_cam/rgb/image_raw",qos)
m=Image();m.width=640;m.height=400;m.step=1920;m.encoding="rgb8";m.data=[10,20,30]*(640*400)
deadline=time.monotonic()+18
while time.monotonic()<deadline:
 p.publish(m);rclpy.spin_once(n,timeout_sec=0.1)
n.destroy_node();rclpy.shutdown()
'''.encode()
   pub=subprocess.Popen(base,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
   pub.stdin.write(publisher);pub.stdin.close()
   try:
    graph=observe('ros-inspect')['camera']
    assert graph['publisher_count']==1 and len(graph['publishers'])==1,graph
    offered=graph['publishers'][0]['offered_qos']
    assert offered['reliability']==reliability and offered['durability']==durability,graph
    result=observe('camera-one-frame')
    assert base64.b64decode(result['pixels'])==bytes([10,20,30])*(640*400)
    requested=result['camera']['requested_qos']
    assert requested['reliability']=='RELIABLE' and requested['durability']=='VOLATILE'
    attempts=result['delivery']['attempts']
    if reliability=='RELIABLE':
     assert len(attempts)==1 and attempts[0]['reliability']=='RELIABLE',attempts
    else:
     assert len(attempts)==2 and attempts[0]['max_matched_publishers']==0,attempts
     assert attempts[1]['reliability']=='BEST_EFFORT',attempts
    assert attempts[-1]['messages_received']==1 and attempts[-1]['max_matched_publishers']==1,attempts
    print(f'Captured one non-root RGB frame: writer {reliability}/{durability}; offered QoS/count verified',flush=True)
   finally:
    pub.wait(timeout=22)
    assert pub.returncode==0,pub.stderr.read().decode()[:2000]
 missing_started=time.monotonic()
 missing=subprocess.run(base+['camera-one-frame'],input=script,capture_output=True,timeout=25)
 assert missing.returncode!=0 and b'TimeoutError' in missing.stderr
 report=json.loads(missing.stdout)['camera_timeout']
 assert report['publisher_count']==0 and len(report['attempts'])==2,report
 assert all(a['messages_received']==0 and a['max_matched_publishers']==0 for a in report['attempts']),report
 assert time.monotonic()-missing_started<23
 print('Missing camera publisher fails within the bounded timeout',flush=True)
finally:
 subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,check=True)
