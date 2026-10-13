from pathlib import Path
import json,subprocess,time,base64,os
script=(Path(__file__).resolve().parents[1]/'deploy/runner/container_diagnostics.py').read_bytes()
image=os.environ.get('NEXARM_DIAG_TEST_IMAGE','nexarm:diagnostics')
container=subprocess.check_output(['docker','run','-d','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp','--tmpfs','/results',image],text=True).strip()
base=['docker','exec','-i','--user','65534:65534','--env','HOME=/tmp','--env','ROS_LOG_DIR=/tmp','--env','LD_LIBRARY_PATH=/opt/ros/humble/lib','--env','AMENT_PREFIX_PATH=/opt/ros/humble',container,'/usr/bin/python3','-I','-']
try:
 graph=subprocess.run(base+['ros-inspect'],input=script,capture_output=True,timeout=25)
 print('Graph exit:',graph.returncode,'stderr:',graph.stderr.decode()[:1000])
 assert graph.returncode==0
 print('Graph:',json.loads(graph.stdout))
 # Separate synthetic publisher in the SAME isolated, disposable container.
 publisher=b'''import sys,time\nsys.path.insert(0,"/opt/ros/humble/local/lib/python3.10/dist-packages")\nsys.path.insert(0,"/opt/ros/humble/lib/python3.10/site-packages")\nimport rclpy\nfrom sensor_msgs.msg import Image\nfrom rclpy.qos import qos_profile_sensor_data\nrclpy.init();n=rclpy.create_node("synthetic_aurora")\np=n.create_publisher(Image,"/depth_cam/rgb/image_raw",qos_profile_sensor_data)\nm=Image();m.width=640;m.height=400;m.step=1920;m.encoding="rgb8";m.data=[10,20,30]*(640*400)\ndeadline=time.monotonic()+20\nwhile time.monotonic()<deadline:\n p.publish(m);rclpy.spin_once(n,timeout_sec=0.1)\nn.destroy_node();rclpy.shutdown()\n'''
 pub=subprocess.Popen(base,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 pub.stdin.write(publisher);pub.stdin.close()
 camera=subprocess.run(base+['camera-one-frame'],input=script,capture_output=True,timeout=25)
 print('Camera exit:',camera.returncode,'stderr:',camera.stderr.decode()[:1000])
 if camera.returncode:
  pub.wait(timeout=25);print('Publisher stderr:',pub.stderr.read().decode()[:2000])
 assert camera.returncode==0
 result=json.loads(camera.stdout);assert base64.b64decode(result['pixels'])==bytes([10,20,30])*(640*400)
 print('Synthetic 640x400 Aurora frame captured as non-root without shell or persistent container payload')
 pub.wait(timeout=25)
 missing=subprocess.run(base+['camera-one-frame'],input=script,capture_output=True,timeout=25)
 assert missing.returncode!=0 and b'TimeoutError' in missing.stderr
 print('Missing camera publisher fails within the bounded timeout')
finally:
 subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,check=True)
