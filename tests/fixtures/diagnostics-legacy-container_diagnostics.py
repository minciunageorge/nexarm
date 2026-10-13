"""Fixed transient ROS observer. No shell, actuator publishers, driver launch or image-file output."""
import base64
import json
import os
import resource
import signal
import sys
import time

# Default SIGALRM terminates even if middleware/native code blocks; killing the
# Docker client alone would otherwise leave docker exec running in the container.
signal.alarm(22)
resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024,512*1024*1024))
resource.setrlimit(resource.RLIMIT_CPU, (15,15))
if sys.version_info[:2] != (3,10):
    raise SystemExit('Expected ROS Humble Python 3.10')
for path in ['/opt/ros/humble/local/lib/python3.10/dist-packages','/opt/ros/humble/lib/python3.10/site-packages']:
    sys.path.insert(0,path)
import rclpy
from rclpy.qos import qos_profile_sensor_data
from rclpy.utilities import get_rmw_implementation_identifier
from sensor_msgs.msg import Image

operation=sys.argv[1]
if operation not in ('ros-inspect','camera-one-frame'): raise SystemExit('Unsupported observer operation')
rclpy.init()
node=rclpy.create_node('nexarm_diagnostic_observer',enable_rosout=False,start_parameter_services=False)
try:
    if operation=='ros-inspect':
        deadline=time.monotonic()+3
        while time.monotonic()<deadline: rclpy.spin_once(node,timeout_sec=0.1)
        nodes=node.get_node_names_and_namespaces()
        topics=node.get_topic_names_and_types()
        if len(nodes)>200 or len(topics)>200: raise RuntimeError('Graph size limit exceeded')
        print(json.dumps({'nodes':sorted(nodes),'topics':sorted(topics), 'ros_domain_id':os.environ.get('ROS_DOMAIN_ID','0'), 'rmw':get_rmw_implementation_identifier()}),flush=True)
    else:
        frame=[]
        subscription=node.create_subscription(Image,'/depth_cam/rgb/image_raw',frame.append,qos_profile_sensor_data)
        deadline=time.monotonic()+15
        while not frame and time.monotonic()<deadline: rclpy.spin_once(node,timeout_sec=0.1)
        if not frame: raise TimeoutError('No Aurora RGB frame within 15 seconds')
        message=frame[0]
        if (message.width,message.height)!=(640,400) or message.encoding not in ('rgb8','bgr8'):
            raise ValueError('Expected a 640x400 rgb8/bgr8 Aurora frame')
        if not 1920<=message.step<=4096 or len(message.data)!=message.step*400:
            raise ValueError('Invalid/oversized image stride')
        pixels=bytearray()
        for y in range(400):
            row=bytes(message.data[y*message.step:y*message.step+1920])
            if message.encoding=='bgr8':
                row=bytes(value for x in range(0,len(row),3) for value in (row[x+2],row[x+1],row[x]))
            pixels.extend(row)
        print(json.dumps({'topic':'/depth_cam/rgb/image_raw','width':640,'height':400,
                          'encoding':'rgb8','ros_domain_id':os.environ.get('ROS_DOMAIN_ID','0'),'rmw':get_rmw_implementation_identifier(),'pixels':base64.b64encode(pixels).decode(),
                          'stamp_sec':message.header.stamp.sec,'stamp_nanosec':message.header.stamp.nanosec,
                          'frame_id':message.header.frame_id[:256]}),flush=True)
finally:
    node.destroy_node();rclpy.shutdown()
