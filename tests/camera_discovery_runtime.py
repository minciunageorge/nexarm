"""Read-only discovery/refusal on a synthetic camera in a disposable container."""
from pathlib import Path
import json,os,subprocess
script=(Path(__file__).resolve().parents[1]/'deploy/runner/container_diagnostics.py').read_bytes()
image=os.environ.get('NEXARM_DIAG_TEST_IMAGE','nexarm:diagnostics')
container=subprocess.check_output(['docker','run','-d','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp','--tmpfs','/results',image],text=True).strip()
base=['docker','exec','-i','--user','65534:65534','--env','HOME=/tmp','--env','ROS_LOG_DIR=/tmp','--env','LD_LIBRARY_PATH=/opt/ros/humble/lib','--env','AMENT_PREFIX_PATH=/opt/ros/humble',container,'/usr/bin/python3','-I','-']
source=b'''import sys,time
sys.path.insert(0,"/opt/ros/humble/local/lib/python3.10/dist-packages")
sys.path.insert(0,"/opt/ros/humble/lib/python3.10/site-packages")
import rclpy
from sensor_msgs.msg import Image
from rclpy.qos import QoSProfile,ReliabilityPolicy
rclpy.init();n=rclpy.create_node("depth_cam")
p=[n.create_publisher(Image,"/depth_cam/"+c+"/image_raw",QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE)) for c in ["rgb","depth","ir"]]
deadline=time.monotonic()+24
while time.monotonic()<deadline:rclpy.spin_once(n,timeout_sec=0.1)
n.destroy_node();rclpy.shutdown()
'''
try:
 pub=subprocess.Popen(base+['/opt/ros/humble/bin/ros2','launch','deptrum-ros-driver-aurora930','aurora930_launch.py'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 pub.stdin.write(source);pub.stdin.close()
 try:
  for operation in ['aurora-discover','aurora-recover']:
   result=subprocess.run(base+[operation],input=script,capture_output=True,timeout=25)
   assert result.returncode==0,result.stderr.decode()[:2000]
   report=json.loads(result.stdout)
   assert report['operation']==operation and report['recovery']['enabled'] is False
   assert report['recovery']['status']=='refused'
   assert any(p['official_launch'] and p['node_pid_mapping_proven'] is False for p in report['processes']),report
   assert any(n['name']=='/depth_cam' for n in report['nodes']),report
   assert all('/depth_cam' in p['nodes'] for p in report['camera_publishers'].values()),report
   if operation=='aurora-recover':assert report['health']['summary']=='all_observed_streams_silent',report
   print(operation+': known launch/node found; camera restart remains refused; safe device metadata collected',flush=True)
 finally:
  pub.wait(timeout=30)
  assert pub.returncode==0,pub.stderr.read().decode()[:2000]
finally:
 subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,check=True)
