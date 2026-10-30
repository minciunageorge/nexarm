import math
import os
import time

import cv2
import message_filters
import numpy as np
import rclpy
import yaml
from rclpy.node import Node
from ros_robot_controller_msgs.msg import ArmCoords, ArmFullState
from sensor_msgs.msg import CameraInfo, Image

from app import calibrated_pose
from app.utils import utils

CONFIG_PATH = "/home/ubuntu/ros2_ws/src/app/config/"
CAMERA_INFO_PATH = "/home/ubuntu/ros2_ws/src/peripherals/config/camera_info.yaml"

with open(CONFIG_PATH + "transform.yaml", encoding="utf-8") as f:
    TRANSFORM = yaml.safe_load(f)
with open(CAMERA_INFO_PATH, encoding="utf-8") as f:
    CAMERA_CFG = yaml.safe_load(f)

PLANE = tuple(float(v) for v in TRANSFORM["plane"])
HAND2CAM = np.asarray(CAMERA_CFG["hand2cam_tf_matrix"], dtype=np.float64)
CAL = calibrated_pose.load_axis_calibration(CONFIG_PATH, "calibration.yaml")

MOVE_ENABLED = os.environ.get("CAPSUNA_V4_MOVE", "0") == "1"

HOME_X, HOME_Y, HOME_Z = 200.0, 0.0, 200.0
SEARCH_MIN_Z = 100.0
SEARCH_STEP_MM = 10.0
SEARCH_SETTLE_S = 0.28
SEARCH_FRAMES = 14
MIN_LINE_HITS = 5
CENTER_TOL_PX = 12.0
ANGLE_TOL_DEG = 12.0

# Thin green target detector. No red fruit is required in V4.
GREEN_LOW = np.array([30, 35, 25], dtype=np.uint8)
GREEN_HIGH = np.array([95, 255, 255], dtype=np.uint8)
HOUGH_THRESHOLD = 6
MIN_LINE_LENGTH_PX = 8
MAX_LINE_GAP_PX = 14
MIN_GREEN_SUPPORT = 0.25

ROI_RADIUS = 5
MIN_VALID_DEPTH_PIXELS = 10
MIN_DEPTH_MM = 180.0
MAX_DEPTH_MM = 1000.0
MIN_PICK_Z_MM = -10.0
MAX_PICK_Z_MM = 110.0
MAX_3D_SPREAD_MM = 15.0

CLAW_TARGET = 64.0
DEST_X, DEST_Y = 310.0, 150.0
DEST_ROLL = -25.8


def angle_diff_180(a, b):
    return abs((a - b + 90.0) % 180.0 - 90.0)


def median_angle_180(values):
    # Double-angle trick: a line is identical after 180 degrees.
    r = np.deg2rad(np.asarray(values, dtype=np.float64) * 2.0)
    s = float(np.mean(np.sin(r)))
    c = float(np.mean(np.cos(r)))
    return math.degrees(math.atan2(s, c)) / 2.0


def wrist_roll_from_image_line(line_angle):
    # Same image-angle-to-wrist calibration that was proven on the strawberry stem.
    delta = (line_angle - 160.2 + 90.0) % 180.0 - 90.0
    roll = 80.0 + delta
    while roll > 90.0:
        roll -= 180.0
    while roll < -90.0:
        roll += 180.0
    return roll


class StrawberryV4(Node):
    def __init__(self):
        super().__init__("strawberry_autopick_v4")
        self.pub = self.create_publisher(ArmCoords, "/ros_robot_controller/arm/set_coords", 10)
        self.create_subscription(ArmFullState, "/ros_robot_controller/arm/full_state", self.arm_state_cb, 5)

        self.current_pose = None
        self.commanded_pose = {
            "x": HOME_X, "y": HOME_Y, "z": HOME_Z,
            "pitch": -90.0, "roll": 0.0, "claw": -100.0, "yaw": 0.0,
        }
        self.latest = None
        self.frame_seq = 0

        rgb_sub = message_filters.Subscriber(self, Image, "/aurora/rgb/image_raw")
        depth_sub = message_filters.Subscriber(self, Image, "/aurora/depth/image_raw")
        info_sub = message_filters.Subscriber(self, CameraInfo, "/aurora/rgb/camera_info")
        self.sync = message_filters.ApproximateTimeSynchronizer(
            [rgb_sub, depth_sub, info_sub], queue_size=8, slop=0.20
        )
        self.sync.registerCallback(self.rgbd_cb)

    def arm_state_cb(self, msg):
        self.current_pose = {
            "x": float(msg.x), "y": float(msg.y), "z": float(msg.z),
            "pitch": float(msg.pitch), "roll": float(msg.roll),
            "claw": float(msg.claw), "yaw": float(msg.yaw),
        }

    def rgbd_cb(self, rgb_msg, depth_msg, info_msg):
        bgr = np.frombuffer(rgb_msg.data, np.uint8).reshape(rgb_msg.height, rgb_msg.width, 3).copy()
        depth = np.frombuffer(depth_msg.data, np.uint16).reshape(depth_msg.height, depth_msg.width).copy()
        self.frame_seq += 1
        self.latest = (self.frame_seq, bgr, depth, list(info_msg.k))

    def controller_ready(self, timeout_s=2.0):
        end = time.time() + timeout_s
        while time.time() < end and rclpy.ok():
            if self.pub.get_subscription_count() > 0:
                return True
            rclpy.spin_once(self, timeout_sec=0.05)
        return self.pub.get_subscription_count() > 0

    def send(self, x, y, z, pitch, roll, claw, ms, extra=0.0):
        q = ArmCoords()
        q.x = float(x); q.y = float(y); q.z = float(z)
        q.pitch = float(pitch); q.roll = float(roll); q.claw = float(claw)
        q.time_ms = int(ms)
        self.pub.publish(q)
        self.commanded_pose = {
            "x": float(x), "y": float(y), "z": float(z),
            "pitch": float(pitch), "roll": float(roll),
            "claw": float(claw), "yaw": 0.0,
        }
        end = time.time() + ms / 1000.0 + extra
        while rclpy.ok() and time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.03)

    def go_home(self, claw=-100.0):
        if MOVE_ENABLED and self.controller_ready(0.8):
            self.send(HOME_X, HOME_Y, HOME_Z, -90.0, 0.0, claw, 500, 0.15)
        print("CAPSUNA_V4_HOME", flush=True)

    def pose_snapshot(self):
        return self.current_pose or self.commanded_pose

    def endpoint_matrix(self):
        p = self.pose_snapshot()
        x_m = p["x"] / 1000.0
        y_m = p["y"] / 1000.0
        z_m = p["z"] / 1000.0
        yaw = math.radians(float(p.get("yaw", 0.0)))
        pitch = math.radians(-float(p["pitch"]))
        roll = math.radians(float(p["roll"]))

        cy, sy = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)
        cr, sr = math.cos(roll), math.sin(roll)
        rz = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
        ry = np.array([[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]])
        rx = np.array([[1.0, 0.0, 0.0], [0.0, cr, -sr], [0.0, sr, cr]])
        endpoint = np.eye(4, dtype=np.float64)
        endpoint[:3, :3] = rz @ ry @ rx
        endpoint[:3, 3] = [x_m, y_m, z_m]
        return endpoint

    def detect_green_wire(self, bgr):
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        raw = cv2.inRange(hsv, GREEN_LOW, GREEN_HIGH)

        # Preserve a sub-pixel/thin green trace, but connect tiny gaps for line detection.
        connected = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=1)
        thick = cv2.dilate(connected, np.ones((3, 3), np.uint8), iterations=1)

        lines = cv2.HoughLinesP(
            thick, 1, np.pi / 180.0,
            threshold=HOUGH_THRESHOLD,
            minLineLength=MIN_LINE_LENGTH_PX,
            maxLineGap=MAX_LINE_GAP_PX,
        )
        if lines is None:
            return None

        h, w = raw.shape[:2]
        best = None
        for item in lines[:, 0, :]:
            x1, y1, x2, y2 = map(int, item)
            length = math.hypot(x2 - x1, y2 - y1)
            if length < MIN_LINE_LENGTH_PX:
                continue

            samples = max(8, int(length))
            xs = np.clip(np.rint(np.linspace(x1, x2, samples)).astype(int), 0, w - 1)
            ys = np.clip(np.rint(np.linspace(y1, y2, samples)).astype(int), 0, h - 1)
            support = 0
            for x, y in zip(xs, ys):
                y0, y1s = max(0, y - 1), min(h, y + 2)
                x0, x1s = max(0, x - 1), min(w, x + 2)
                if np.any(raw[y0:y1s, x0:x1s] > 0):
                    support += 1
            support_ratio = support / float(samples)
            if support_ratio < MIN_GREEN_SUPPORT:
                continue

            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            score = length * (0.5 + support_ratio)
            candidate = (score, cx, cy, angle, length, support_ratio, (x1, y1, x2, y2))
            if best is None or candidate[0] > best[0]:
                best = candidate

        if best is None:
            return None
        _, cx, cy, angle, length, support_ratio, segment = best
        return {
            "center": (float(cx), float(cy)),
            "angle": float(angle),
            "length": float(length),
            "support": float(support_ratio),
            "segment": segment,
        }

    def depth_roi(self, depth, x, y):
        h, w = depth.shape[:2]
        x = int(round(x)); y = int(round(y))
        x0, x1 = max(0, x - ROI_RADIUS), min(w, x + ROI_RADIUS + 1)
        y0, y1 = max(0, y - ROI_RADIUS), min(h, y + ROI_RADIUS + 1)
        roi = depth[y0:y1, x0:x1]
        valid = roi[(roi > 0) & (roi < 10000)]
        if valid.size < MIN_VALID_DEPTH_PIXELS:
            return None, int(valid.size)
        return float(np.median(valid)), int(valid.size)

    def collect_level(self, z_mm):
        detections = []
        seen_seq = -1
        deadline = time.time() + 2.5
        fresh = 0

        while rclpy.ok() and time.time() < deadline and fresh < SEARCH_FRAMES:
            rclpy.spin_once(self, timeout_sec=0.12)
            if self.latest is None:
                continue
            seq, bgr, depth, k = self.latest
            if seq == seen_seq:
                continue
            seen_seq = seq
            fresh += 1
            d = self.detect_green_wire(bgr)
            if d is None:
                print("V4 Z", int(z_mm), "FRAME", fresh, "FARA FIR", flush=True)
                continue

            depth_mm, valid_n = self.depth_roi(depth, d["center"][0], d["center"][1])
            d["depth_mm"] = depth_mm
            d["valid_depth"] = valid_n
            d["k"] = k
            detections.append(d)
            print(
                "V4 Z", int(z_mm), "FRAME", fresh,
                "FIR", round(d["center"][0], 1), round(d["center"][1], 1),
                "ANGLE", round(d["angle"], 1),
                "LEN", round(d["length"], 1),
                "SUP", round(d["support"], 2),
                "DEPTH", None if depth_mm is None else round(depth_mm, 1),
                flush=True,
            )

        if len(detections) < MIN_LINE_HITS:
            return None

        # Find a coherent cluster around median center and line orientation.
        mx = float(np.median([d["center"][0] for d in detections]))
        my = float(np.median([d["center"][1] for d in detections]))
        ma = median_angle_180([d["angle"] for d in detections])
        stable = [
            d for d in detections
            if math.hypot(d["center"][0] - mx, d["center"][1] - my) <= CENTER_TOL_PX
            and angle_diff_180(d["angle"], ma) <= ANGLE_TOL_DEG
        ]
        if len(stable) < MIN_LINE_HITS:
            print("V4 FIR VAZUT DAR INSTABIL", len(stable), "/", len(detections), flush=True)
            return None

        cx = float(np.median([d["center"][0] for d in stable]))
        cy = float(np.median([d["center"][1] for d in stable]))
        angle = median_angle_180([d["angle"] for d in stable])
        lengths = [d["length"] for d in stable]
        depth_values = [d["depth_mm"] for d in stable if d["depth_mm"] is not None]
        depth_mm = float(np.median(depth_values)) if depth_values else None

        return {
            "center": (cx, cy),
            "angle": angle,
            "length": float(np.median(lengths)),
            "depth_mm": depth_mm,
            "k": stable[-1]["k"],
            "hits": len(stable),
            "spread_px": max(math.hypot(d["center"][0] - cx, d["center"][1] - cy) for d in stable),
        }

    def target_world(self, detection):
        depth_mm = detection["depth_mm"]
        if depth_mm is None:
            return None
        cx, cy = detection["center"]
        endpoint = self.endpoint_matrix()
        world = np.asarray(
            utils.calculate_world_position(cx, cy, depth_mm, PLANE, endpoint, HAND2CAM, detection["k"]),
            dtype=np.float64,
        )
        depth_cal = calibrated_pose.apply_axis_calibration(world, CAL, "depth")
        return np.asarray(calibrated_pose.apply_axis_calibration(depth_cal, CAL, "kinematics"), dtype=np.float64)

    def execute_pick(self, xyz, roll):
        x, y, z = [float(v) * 1000.0 for v in xyz]
        if not (MIN_PICK_Z_MM <= z <= MAX_PICK_Z_MM):
            print("CAPSUNA_V4_ABORT_Z_RANGE", round(z, 1), flush=True)
            self.go_home()
            return

        print("CAPSUNA_V4_PICK", round(x, 1), round(y, 1), round(z, 1), "ROLL", round(roll, 1), flush=True)
        self.send(x, y, z + 45.0, -90.0, roll, -100.0, 320, 0.25)
        self.send(x, y, z, -90.0, roll, -100.0, 280, 0.05)
        self.send(x, y, z, -90.0, roll, CLAW_TARGET, 180, 0.35)
        self.send(x, y, z + 80.0, -90.0, roll, CLAW_TARGET, 350, 0.05)
        self.send(HOME_X, HOME_Y, HOME_Z, -90.0, 0.0, CLAW_TARGET, 650, 0.10)
        self.send(DEST_X, DEST_Y, 80.0, -80.0, DEST_ROLL, CLAW_TARGET, 650, 0.10)
        self.send(DEST_X, DEST_Y, 10.0, -80.0, DEST_ROLL, CLAW_TARGET, 450, 0.08)
        self.send(DEST_X, DEST_Y, 10.0, -80.0, DEST_ROLL, -100.0, 180, 0.30)
        self.go_home(-100.0)
        print("CAPSUNA_V4_GATA", flush=True)

    def run(self):
        if not self.controller_ready():
            print("CAPSUNA_V4_ABORT_NO_CONTROLLER", flush=True)
            return

        # Dry-run never moves the arm; it only tests the thin-wire detector at the current pose.
        if not MOVE_ENABLED:
            z = float(self.pose_snapshot()["z"])
            result = self.collect_level(z)
            print("CAPSUNA_V4_DRYRUN_RESULT", result, flush=True)
            return

        # Always start from a known HOME observation pose.
        self.go_home(-100.0)

        z = HOME_Z
        while z >= SEARCH_MIN_Z - 0.1 and rclpy.ok():
            if z < HOME_Z:
                print("CAPSUNA_V4_SEARCH_DOWN_TO", int(z), flush=True)
                self.send(HOME_X, HOME_Y, z, -90.0, 0.0, -100.0, 260, SEARCH_SETTLE_S)
            else:
                # Let fresh frames arrive at HOME.
                end = time.time() + SEARCH_SETTLE_S
                while rclpy.ok() and time.time() < end:
                    rclpy.spin_once(self, timeout_sec=0.03)

            result = self.collect_level(z)
            if result is not None:
                print(
                    "CAPSUNA_V4_FIR_GASIT ZCAM", int(z),
                    "CENTER", round(result["center"][0], 1), round(result["center"][1], 1),
                    "ANGLE", round(result["angle"], 1),
                    "HITS", result["hits"],
                    "DEPTH", None if result["depth_mm"] is None else round(result["depth_mm"], 1),
                    flush=True,
                )

                # User requested that descent stops as soon as the wire is seen.
                if result["depth_mm"] is None or not (MIN_DEPTH_MM <= result["depth_mm"] <= MAX_DEPTH_MM):
                    print("CAPSUNA_V4_FIR_VAZUT_DAR_FARA_DEPTH_SIGUR", flush=True)
                    self.go_home()
                    return

                xyz = self.target_world(result)
                if xyz is None or not np.all(np.isfinite(xyz)):
                    print("CAPSUNA_V4_ABORT_XYZ", flush=True)
                    self.go_home()
                    return

                print("CAPSUNA_V4_XYZ", *(round(float(v) * 1000.0, 1) for v in xyz), "mm", flush=True)
                roll = wrist_roll_from_image_line(result["angle"])
                self.execute_pick(xyz, roll)
                return

            z -= SEARCH_STEP_MM

        print("CAPSUNA_V4_NU_A_GASIT_FIRUL", flush=True)
        self.go_home()


rclpy.init()
node = StrawberryV4()
try:
    node.run()
finally:
    node.destroy_node()
    rclpy.shutdown()
