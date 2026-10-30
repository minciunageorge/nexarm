import time
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo

rclpy.init()
node = Node("capsuna_v3_rgbd_probe")
state = {"rgb": None, "depth": None, "rgbinfo": None, "irinfo": None}

def rgb_cb(m):
    state["rgb"] = (m.width, m.height, m.encoding, m.step)

def depth_cb(m):
    a = np.frombuffer(m.data, dtype=np.uint16).reshape(m.height, m.width)
    v = a[(a > 0) & (a < 10000)]
    state["depth"] = (
        m.width, m.height, m.encoding, m.step,
        int(np.median(v)) if v.size else None,
        int(v.min()) if v.size else None,
        int(v.max()) if v.size else None,
    )

def rgb_info_cb(m):
    state["rgbinfo"] = (m.width, m.height, list(m.k))

def ir_info_cb(m):
    state["irinfo"] = (m.width, m.height, list(m.k))

node.create_subscription(Image, "/aurora/rgb/image_raw", rgb_cb, 10)
node.create_subscription(Image, "/aurora/depth/image_raw", depth_cb, 10)
node.create_subscription(CameraInfo, "/aurora/rgb/camera_info", rgb_info_cb, 10)
node.create_subscription(CameraInfo, "/aurora/ir/camera_info", ir_info_cb, 10)

end = time.time() + 8.0
while time.time() < end and rclpy.ok():
    rclpy.spin_once(node, timeout_sec=0.2)
    if state["rgb"] and state["depth"] and (state["rgbinfo"] or state["irinfo"]):
        break

for key, value in state.items():
    print(key, "=", value, flush=True)

node.destroy_node()
rclpy.shutdown()
