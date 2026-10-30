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
from ros_robot_controller_msgs.srv import GetArmFullState
from sensor_msgs.msg import CameraInfo, Image

from app import calibrated_pose
from app.utils import utils
from sdk import common

CONFIG_PATH = "/home/ubuntu/ros2_ws/src/app/config/"
CAMERA_INFO_PATH = "/home/ubuntu/ros2_ws/src/peripherals/config/camera_info.yaml"

with open(CONFIG_PATH + "transform.yaml", encoding="utf-8") as f:
    TRANSFORM = yaml.safe_load(f)
with open(CAMERA_INFO_PATH, encoding="utf-8") as f:
    CAMERA_CFG = yaml.safe_load(f)

PLANE = tuple(float(v) for v in TRANSFORM["plane"])
HAND2CAM = np.asarray(CAMERA_CFG["hand2cam_tf_matrix"], dtype=np.float64)
CAL = calibrated_pose.load_axis_calibration(CONFIG_PATH, "calibration.yaml")

# Old proven 2D calibration is kept as an independent cross-check.
FX, FY, CX, CY = 414.961669921875, 415.7837219238281, 322.6287536621094, 192.77626037597656
K_OLD = np.matrix([[FX, 0, CX], [0, FY, CY], [0, 0, 1]], dtype=np.float64)
E = np.array(TRANSFORM["extristric"], dtype=np.float64)
TVEC, RMAT = common.extristric_plane_shift(E[:1].reshape(3, 1), E[1:], 0.03)
WHITE_WORLD = np.array(TRANSFORM["white_area_pose_world"], dtype=np.float64)

MOVE_ENABLED = os.environ.get("CAPSUNA_V3_MOVE", "0") == "1"

MAX_FRAMES = 30
MIN_FINAL_HITS = 6
STABLE_REQUIRED = 8
STABLE_POSITION_M = 0.010
STABLE_ANGLE_DEG = 8.0
MAX_STEM_DISTANCE_PX = 150.0
ROI_RADIUS = 5
MIN_VALID_DEPTH_PIXELS = 12
MIN_DEPTH_MM = 200.0
MAX_DEPTH_MM = 1000.0
MAX_XY_DISAGREEMENT_MM = 35.0
MAX_3D_SPREAD_MM = 12.0
MIN_PICK_Z_MM = -10.0
MAX_PICK_Z_MM = 100.0
CLAW_STRAWBERRY = 64.0

# Fast proven destination/trajectory from BACKUP-005.
DEST_X, DEST_Y = 310.0, 150.0
DEST_ROLL = -25.8


def contours(mask, min_area):
    cs = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
    return [c for c in cs if cv2.contourArea(c) > min_area]


def center(c):
    m = cv2.moments(c)
    if m["m00"] == 0:
        return None
    return int(m["m10"] / m["m00"]), int(m["m01"] / m["m00"])


def angle_diff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def pair_target(red_contours, green_contours):
    green_centers = [(g, center(g)) for g in green_contours]
    green_centers = [(g, p) for g, p in green_centers if p is not None]
    candidates = []
    for red in red_contours:
        rp = center(red)
        if rp is None:
            continue
        nearby = []
        for green, gp in green_centers:
            d = math.hypot(gp[0] - rp[0], gp[1] - rp[1])
            if d <= MAX_STEM_DISTANCE_PX:
                nearby.append((d, gp, green))
        if nearby:
            d, gp, green = min(nearby, key=lambda item: item[0])
            candidates.append((cv2.contourArea(red), -d, red, rp, green, gp, d))
    return max(candidates, key=lambda item: (item[0], item[1])) if candidates else None


class StrawberryV3(Node):
    def __init__(self):
        super().__init__("strawberry_autopick_v3")
        self.pub = self.create_publisher(ArmCoords, "/ros_robot_controller/arm/set_coords", 10)
        self.create_subscription(ArmFullState, "/ros_robot_controller/arm/full_state", self.arm_state_cb, 5)
        self.state_client = self.create_client(GetArmFullState, "/ros_robot_controller/arm/get_full_state")

        self.frames = 0
        self.hits = []
        self.done = False
        self.stable_count = 0
        self.previous_pos = None
        self.previous_angle = None
        self.current_pose = None
        self.known_pose = {
            "x": 200.0, "y": 0.0, "z": 200.0,
            "pitch": -90.0, "roll": 0.0, "claw": -100.0, "yaw": 0.0,
        }

        rgb_sub = message_filters.Subscriber(self, Image, "/aurora/rgb/image_raw")
        depth_sub = message_filters.Subscriber(self, Image, "/aurora/depth/image_raw")
        info_sub = message_filters.Subscriber(self, CameraInfo, "/aurora/rgb/camera_info")
        self.sync = message_filters.ApproximateTimeSynchronizer(
            [rgb_sub, depth_sub, info_sub], queue_size=6, slop=0.20
        )
        self.sync.registerCallback(self.callback)

    def arm_state_cb(self, msg):
        self.current_pose = {
            "x": float(msg.x), "y": float(msg.y), "z": float(msg.z),
            "pitch": float(msg.pitch), "roll": float(msg.roll),
            "claw": float(msg.claw), "yaw": float(msg.yaw),
        }

    def request_pose(self, timeout_s=0.35):
        if not self.state_client.wait_for_service(timeout_sec=0.15):
            return None
        fut = self.state_client.call_async(GetArmFullState.Request())
        end = time.time() + timeout_s
        while rclpy.ok() and time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.02)
            if fut.done():
                try:
                    res = fut.result()
                except Exception:
                    return None
                if res is None or not res.success:
                    return None
                return {
                    "x": float(res.x), "y": float(res.y), "z": float(res.z),
                    "pitch": float(res.pitch), "roll": float(res.roll),
                    "claw": float(res.claw), "yaw": float(res.yaw),
                }
        return None

    def pose_snapshot(self):
        return self.current_pose or self.request_pose() or self.known_pose

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
        rclpy.spin_once(self, timeout_sec=0.05)
        time.sleep(ms / 1000.0 + extra)

    def depth_roi(self, depth_image, x, y):
        h, w = depth_image.shape[:2]
        x0, x1 = max(0, x - ROI_RADIUS), min(w, x + ROI_RADIUS)
        y0, y1 = max(0, y - ROI_RADIUS), min(h, y + ROI_RADIUS)
        roi = depth_image[y0:y1, x0:x1]
        valid = roi[(roi > 0) & (roi < 10000)]
        if valid.size < MIN_VALID_DEPTH_PIXELS:
            return None, int(valid.size)
        return float(np.median(valid)), int(valid.size)

    def execute(self, x_m, y_m, z_m, roll):
        x = x_m * 1000.0
        y = y_m * 1000.0
        z = z_m * 1000.0
        if not self.controller_ready():
            print("CAPSUNA_V3_ABORT_NO_CONTROLLER", flush=True)
            return

        print("CAPSUNA_V3_MOVE", round(x, 1), round(y, 1), round(z, 1), "ROLL", round(roll, 1), flush=True)
        self.send(x, y, z + 45.0, -90.0, roll, -100.0, 320, 0.35)
        self.send(x, y, z, -90.0, roll, -100.0, 280, 0.05)
        self.send(x, y, z, -90.0, roll, CLAW_STRAWBERRY, 180, 0.40)
        self.send(x, y, z + 80.0, -90.0, roll, CLAW_STRAWBERRY, 350, 0.05)

        self.send(200, 0, 200, -90, 0, CLAW_STRAWBERRY, 700, 0.15)
        self.send(DEST_X, DEST_Y, 80, -80, DEST_ROLL, CLAW_STRAWBERRY, 700, 0.10)
        self.send(DEST_X, DEST_Y, 10, -80, DEST_ROLL, CLAW_STRAWBERRY, 500, 0.10)
        self.send(DEST_X, DEST_Y, 10, -80, DEST_ROLL, -100, 180, 0.40)
        self.send(200, 0, 200, -90, 0, -100, 350, 0.20)
        print("CAPSUNA_V3_GATA", flush=True)

    def finalize(self):
        self.done = True
        print("==============================", flush=True)
        print(f"CAPSUNA V3 HITS = {len(self.hits)}/{self.frames}", flush=True)
        print(f"STABLE = {self.stable_count}/{STABLE_REQUIRED}", flush=True)
        if len(self.hits) < MIN_FINAL_HITS:
            print("CAPSUNA_V3_ABORT_PREA_PUTINE_DETECTII", flush=True)
            return

        selected = self.hits[-(STABLE_REQUIRED + 1):] if self.stable_count >= STABLE_REQUIRED else self.hits
        gx = int(np.median([h["gp"][0] for h in selected]))
        gy = int(np.median([h["gp"][1] for h in selected]))
        rx = int(np.median([h["rp"][0] for h in selected]))
        ry = int(np.median([h["rp"][1] for h in selected]))
        depth_mm = float(np.median([h["depth_mm"] for h in selected]))

        exec_xyz = np.median(np.array([h["exec_xyz"] for h in selected], dtype=np.float64), axis=0)
        old_xyz = np.median(np.array([h["old_xyz"] for h in selected], dtype=np.float64), axis=0)

        stem_angle = math.degrees(math.atan2(gy - ry, gx - rx))
        # Proven BACKUP-005 strawberry wrist calibration.
        roll = 80.0 + (stem_angle - 160.2)
        while roll > 90.0:
            roll -= 180.0
        while roll < -90.0:
            roll += 180.0

        spread_mm = max(float(np.linalg.norm(np.asarray(h["exec_xyz"]) - exec_xyz) * 1000.0) for h in selected)
        xy_diff_mm = math.hypot(exec_xyz[0] - old_xyz[0], exec_xyz[1] - old_xyz[1]) * 1000.0
        z_mm = exec_xyz[2] * 1000.0

        print("CODITA FINAL =", gx, gy, flush=True)
        print("DEPTH MEDIAN =", round(depth_mm, 1), "mm", flush=True)
        print("XYZ DEPTH EXEC =", *(round(float(v) * 1000.0, 1) for v in exec_xyz), "mm", flush=True)
        print("XY VECHI =", round(float(old_xyz[0]) * 1000.0, 1), round(float(old_xyz[1]) * 1000.0, 1), "mm", flush=True)
        print("XY DIF =", round(xy_diff_mm, 1), "mm", flush=True)
        print("3D SPREAD =", round(spread_mm, 1), "mm", flush=True)
        print("STEM ANGLE =", round(stem_angle, 1), "ROLL =", round(roll, 1), flush=True)
        print("MOVE_ENABLED =", MOVE_ENABLED, flush=True)
        print("==============================", flush=True)

        if self.stable_count < STABLE_REQUIRED and spread_mm > MAX_3D_SPREAD_MM:
            print("CAPSUNA_V3_ABORT_INSTABILA_3D", flush=True)
            return
        if not (MIN_DEPTH_MM <= depth_mm <= MAX_DEPTH_MM):
            print("CAPSUNA_V3_ABORT_DEPTH_RANGE", flush=True)
            return
        if xy_diff_mm > MAX_XY_DISAGREEMENT_MM:
            print("CAPSUNA_V3_ABORT_2D_3D_DISAGREE", flush=True)
            return
        if not (MIN_PICK_Z_MM <= z_mm <= MAX_PICK_Z_MM):
            print("CAPSUNA_V3_ABORT_Z_RANGE", flush=True)
            return
        if not MOVE_ENABLED:
            print("CAPSUNA_V3_DRYRUN_OK", flush=True)
            return

        self.execute(float(exec_xyz[0]), float(exec_xyz[1]), float(exec_xyz[2]), float(roll))

    def callback(self, rgb_msg, depth_msg, info_msg):
        if self.done:
            return
        self.frames += 1

        bgr = np.frombuffer(rgb_msg.data, np.uint8).reshape(rgb_msg.height, rgb_msg.width, 3).copy()
        depth = np.frombuffer(depth_msg.data, np.uint16).reshape(depth_msg.height, depth_msg.width).copy()
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

        r1 = cv2.inRange(hsv, np.array([0, 90, 50]), np.array([12, 255, 255]))
        r2 = cv2.inRange(hsv, np.array([170, 90, 50]), np.array([179, 255, 255]))
        red = cv2.bitwise_or(r1, r2)
        green = cv2.inRange(hsv, np.array([35, 50, 35]), np.array([90, 255, 255]))
        pair = pair_target(contours(red, 300), contours(green, 5))

        if pair is None:
            self.stable_count = 0
            self.previous_pos = None
            self.previous_angle = None
            print("FRAME", self.frames, "FARA PERECHE", flush=True)
        else:
            _, _, _, rp, _, gp, dpx = pair
            depth_mm, valid_n = self.depth_roi(depth, gp[0], gp[1])
            if depth_mm is None:
                self.stable_count = 0
                self.previous_pos = None
                print("FRAME", self.frames, "FARA DEPTH VALID", valid_n, flush=True)
            else:
                stem_angle = math.degrees(math.atan2(gp[1] - rp[1], gp[0] - rp[0]))
                endpoint = self.endpoint_matrix()
                world = np.asarray(
                    utils.calculate_world_position(
                        gp[0], gp[1], depth_mm, PLANE, endpoint, HAND2CAM, info_msg.k
                    ),
                    dtype=np.float64,
                )
                depth_cal = calibrated_pose.apply_axis_calibration(world, CAL, "depth")
                exec_xyz = calibrated_pose.apply_axis_calibration(depth_cal, CAL, "kinematics")
                old_xyz, _ = calibrated_pose.pixel_to_calibrated_world(
                    gp, K_OLD, (TVEC, RMAT), WHITE_WORLD, CAL, height=0.03
                )
                old_xyz = np.asarray(old_xyz, dtype=np.float64)

                if self.previous_pos is not None and self.previous_angle is not None:
                    delta = float(np.linalg.norm(exec_xyz - self.previous_pos))
                    if delta < STABLE_POSITION_M and angle_diff(stem_angle, self.previous_angle) < STABLE_ANGLE_DEG:
                        self.stable_count += 1
                    else:
                        self.stable_count = 0
                else:
                    self.stable_count = 0

                self.previous_pos = exec_xyz.copy()
                self.previous_angle = stem_angle
                self.hits.append({
                    "rp": rp, "gp": gp, "distance_px": dpx,
                    "depth_mm": depth_mm, "exec_xyz": exec_xyz,
                    "old_xyz": old_xyz, "angle": stem_angle,
                })
                print(
                    "FRAME", self.frames, "ROSU", rp, "CODITA", gp,
                    "DEPTH", round(depth_mm, 1), "VALID", valid_n,
                    "XYZ", *(round(float(v) * 1000.0, 1) for v in exec_xyz),
                    "STABLE", self.stable_count,
                    flush=True,
                )

        if self.stable_count >= STABLE_REQUIRED or self.frames >= MAX_FRAMES:
            self.finalize()


rclpy.init()
node = StrawberryV3()
while rclpy.ok() and not node.done:
    rclpy.spin_once(node, timeout_sec=1.0)
node.destroy_node()
rclpy.shutdown()
