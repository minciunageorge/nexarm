"""Integration check inside the built container; publishes only synthetic images."""
import json
from pathlib import Path
import subprocess
import time
import uuid

import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

rclpy.init()
node = rclpy.create_node('nexarm_synthetic_camera_test')
topic = '/nexarm_test/image_' + uuid.uuid4().hex
publisher = node.create_publisher(Image, topic, qos_profile_sensor_data)
child = subprocess.Popen(['python3', '/opt/nexarm/camera_capture.py', '--topic', topic,
                          '--output', '/results/camera/test.ppm', '--timeout', '8'])
try:
    message = Image()
    message.width = 2
    message.height = 1
    message.encoding = 'rgb8'
    message.step = 6
    message.data = [255, 0, 0, 0, 255, 0]
    message.header.frame_id = 'synthetic'
    deadline = time.monotonic() + 10
    while child.poll() is None and time.monotonic() < deadline:
        publisher.publish(message)
        rclpy.spin_once(node, timeout_sec=0.1)
    assert child.wait(timeout=2) == 0
    assert Path('/results/camera/test.ppm').read_bytes() == b'P6\n2 1\n255\n\xff\x00\x00\x00\xff\x00'
    assert json.loads(Path('/results/camera/test.json').read_text())['frame_id'] == 'synthetic'
    missing = subprocess.run(['python3', '/opt/nexarm/camera_capture.py', '--topic', topic + '_missing',
                              '--output', '/results/missing.ppm', '--timeout', '0.3'], capture_output=True)
    assert missing.returncode != 0 and b'TimeoutError' in missing.stderr
    print('ROS2 lifecycle, synthetic camera capture, metadata and missing-topic timeout passed')
finally:
    if child.poll() is None:
        child.kill()
        child.wait()
    node.destroy_node()
    rclpy.shutdown()
