"""Fixed transient ROS observer. No shell, actuator publishers, driver launch or image-file output."""
import base64
import ctypes
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
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
from rclpy.utilities import get_rmw_implementation_identifier
from sensor_msgs.msg import Image

RGB_TOPIC = '/depth_cam/rgb/image_raw'
# Primary reader matches the live Aurora RELIABLE/VOLATILE writer. A bounded
# best-effort fallback preserves support for sensor-data writers on this topic.
CAMERA_QOS = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=5,
                        reliability=ReliabilityPolicy.RELIABLE,
                        durability=DurabilityPolicy.VOLATILE)
FALLBACK_QOS = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=5,
                          reliability=ReliabilityPolicy.BEST_EFFORT,
                          durability=DurabilityPolicy.VOLATILE)


# Humble's Python Subscription wrapper lacks get_publisher_count(). Borrow the
# protected rcl_subscription_t pointer for this fixed read-only RCL query.
_rcl = ctypes.CDLL('/opt/ros/humble/lib/librcl.so')
_count_matched = _rcl.rcl_subscription_get_publisher_count
_count_matched.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
_count_matched.restype = ctypes.c_int


def matched_publishers(subscription):
    count = ctypes.c_size_t()
    handle = subscription.handle
    with handle:
        status = _count_matched(ctypes.c_void_p(handle.pointer), ctypes.byref(count))
    if status != 0:
        raise RuntimeError('RCL matched-publisher query failed')
    return count.value


def describe_qos(qos):
    return {'reliability': qos.reliability.name, 'durability': qos.durability.name,
            'history': qos.history.name, 'depth': qos.depth,
            'deadline_ns': qos.deadline.nanoseconds,
            'lifespan_ns': qos.lifespan.nanoseconds,
            'liveliness': qos.liveliness.name,
            'liveliness_lease_duration_ns': qos.liveliness_lease_duration.nanoseconds}


def camera_publishers():
    endpoints = node.get_publishers_info_by_topic(RGB_TOPIC)
    if len(endpoints) > 200:
        raise RuntimeError('Camera publisher limit exceeded')
    return {'topic': RGB_TOPIC, 'publisher_count': node.count_publishers(RGB_TOPIC),
            'publishers': [{'node_name': endpoint.node_name,
                            'node_namespace': endpoint.node_namespace,
                            'topic_type': endpoint.topic_type,
                            'offered_qos': describe_qos(endpoint.qos_profile)}
                           for endpoint in endpoints],
            'requested_qos': describe_qos(CAMERA_QOS),
            'fallback_requested_qos': describe_qos(FALLBACK_QOS)}


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
        print(json.dumps({'nodes':sorted(nodes),'topics':sorted(topics), 'ros_domain_id':os.environ.get('ROS_DOMAIN_ID','0'), 'rmw':get_rmw_implementation_identifier(), 'camera':camera_publishers()}),flush=True)
    else:
        frame=[]
        attempts=[]
        deadline=time.monotonic()+15
        for qos in (CAMERA_QOS,FALLBACK_QOS):
            attempt={'reliability':qos.reliability.name, 'durability':qos.durability.name,
                     'max_matched_publishers':0, 'matched_publishers':0,
                     'messages_received':0}
            attempts.append(attempt)
            def receive(message):
                attempt['messages_received']+=1
                if not frame: frame.append(message)
            subscription=node.create_subscription(Image,RGB_TOPIC,receive,qos)
            try:
                attempt_deadline=min(deadline,time.monotonic()+8) if qos is CAMERA_QOS else deadline
                while not frame and time.monotonic()<attempt_deadline:
                    rclpy.spin_once(node,timeout_sec=0.1)
                    matched=matched_publishers(subscription)
                    attempt['matched_publishers']=matched
                    attempt['max_matched_publishers']=max(attempt['max_matched_publishers'],matched)
            finally:
                node.destroy_subscription(subscription)
            if frame: break
        publishers=camera_publishers()
        delivery={'topic':RGB_TOPIC, 'publisher_count':publishers['publisher_count'],
                  'offered_reliability':[p['offered_qos']['reliability'] for p in publishers['publishers']],
                  'offered_durability':[p['offered_qos']['durability'] for p in publishers['publishers']],
                  'attempts':attempts}
        if not frame:
            # Broker accepts only this bounded schema and exports it to the error
            # artifact; raw container stderr/tracebacks are never forwarded.
            print(json.dumps({'camera_timeout':delivery}),flush=True)
            raise TimeoutError('No Aurora RGB frame within 15 seconds')
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
                          'frame_id':message.header.frame_id[:256], 'camera':publishers, 'selected_qos':describe_qos(qos), 'delivery':delivery}),flush=True)
finally:
    node.destroy_node();rclpy.shutdown()
