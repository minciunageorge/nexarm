import math
import time

import cv2
import numpy as np
import rclpy
import yaml
from ros_robot_controller_msgs.msg import ArmCoords
from sensor_msgs.msg import Image

from app import calibrated_pose
from app.utils import calculate_grasp_yaw, pick_and_place
from sdk import common

# Camera intrinsics verified on Aurora 930.
FX, FY, CX, CY = 414.961669921875, 415.7837219238281, 322.6287536621094, 192.77626037597656
K = np.matrix([[FX, 0, CX], [0, FY, CY], [0, 0, 1]], dtype=np.float64)

CONFIG_PATH = "/home/ubuntu/ros2_ws/src/app/config/"
with open(CONFIG_PATH + "transform.yaml", encoding="utf-8") as f:
    T = yaml.safe_load(f)
E = np.array(T["extristric"], dtype=np.float64)
tvec, rmat = common.extristric_plane_shift(E[:1].reshape(3, 1), E[1:], 0.03)
W = np.array(T["white_area_pose_world"], dtype=np.float64)
CAL = calibrated_pose.load_axis_calibration(CONFIG_PATH, "calibration.yaml")

# Keep the proven strawberry geometry, but use Hiwonder's official pick/place motion helper.
PICK_HEIGHT_M = 0.015       # with gripper_depth=0.020 -> final z becomes 0 mm in current helper
GRIPPER_DEPTH_M = 0.020
CLAW_STRAWBERRY = 64.0
DESTINATION_M = [0.310, 0.150, 0.020]
DESTINATION_PITCH = 80.0
DESTINATION_ROLL = -25.8

MAX_FRAMES = 28
MIN_FINAL_HITS = 6
STABLE_REQUIRED = 8
STABLE_POSITION_M = 0.010   # Hiwonder uses 10 mm position stability threshold
STABLE_ANGLE_DEG = 8.0
MAX_STEM_DISTANCE_PX = 150.0
COLLISION_AREA_PX = 100.0

rclpy.init()
node = rclpy.create_node("strawberry_autopick_v2")
pub = node.create_publisher(ArmCoords, "/ros_robot_controller/arm/set_coords", 10)

frames = 0
hits = []
done = False
stable_count = 0
previous_pos = None
previous_angle = None


def contours(mask, min_area):
    cs = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
    return [c for c in cs if cv2.contourArea(c) > min_area]


def center(c):
    m = cv2.moments(c)
    if m["m00"] == 0:
        return None
    return int(m["m10"] / m["m00"]), int(m["m01"] / m["m00"])


def angle_diff(a, b):
    d = (a - b + 180.0) % 360.0 - 180.0
    return abs(d)


def pair_target(red_contours, green_contours):
    """Choose the largest red fruit that has a nearby green stem candidate."""
    candidates = []
    green_centers = [(g, center(g)) for g in green_contours]
    green_centers = [(g, p) for g, p in green_centers if p is not None]

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

    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))


def controller_ready(timeout_s=3.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline and rclpy.ok():
        if pub.get_subscription_count() > 0:
            return True
        rclpy.spin_once(node, timeout_sec=0.1)
    return pub.get_subscription_count() > 0


def collision_guard(gx, gy, stem_angle, projection_matrix, red_rects, target_rp):
    """Hiwonder-inspired gripper collision check against neighboring red fruits.

    Failure to compute the check does not block a proven pick; a positive collision does.
    """
    try:
        gw = float(common.calculate_pixel_length(0.09, K, projection_matrix))
        gh = float(common.calculate_pixel_length(0.015, K, projection_matrix))
        if not (math.isfinite(gw) and math.isfinite(gh) and gw > 0 and gh > 0):
            return False, "COLLISION_CHECK_SKIPPED_BAD_SIZE"

        gripper_rect = ((float(gx), float(gy)), (gw, gh), float(stem_angle))
        for rect in red_rects:
            # Skip the selected fruit rectangle itself by center proximity.
            rcx, rcy = rect[0]
            if math.hypot(rcx - target_rp[0], rcy - target_rp[1]) < 3.0:
                continue
            area = calculate_grasp_yaw.calculate_intersection_area(gripper_rect, rect)
            if area is not None and area >= COLLISION_AREA_PX:
                return True, f"COLLISION_AREA={area:.1f}"
        return False, f"COLLISION_CLEAR gripper_px={gw:.1f}x{gh:.1f}"
    except Exception as exc:
        return False, f"COLLISION_CHECK_SKIPPED {type(exc).__name__}: {exc}"


def execute_pick(solution):
    gx, gy = solution["stem_px"]
    x_m, y_m = solution["xy_m"]
    roll = solution["roll"]
    stem_angle = solution["stem_angle"]
    projection = solution["projection"]
    red_rects = solution["red_rects"]
    target_rp = solution["target_rp"]

    collision, collision_msg = collision_guard(
        gx, gy, stem_angle, projection, red_rects, target_rp
    )
    print(collision_msg, flush=True)
    if collision:
        print("CAPSUNA_V2_ABORT_COLLISION", flush=True)
        return

    if not controller_ready():
        print("CAPSUNA_V2_ABORT_NO_CONTROLLER_SUBSCRIBER", flush=True)
        return

    home = pick_and_place.load_scene_home_pose()
    print(
        "HOME OFFICIAL =",
        round(float(home["x"]), 1),
        round(float(home["y"]), 1),
        round(float(home["z"]), 1),
        "P", round(float(home["pitch"]), 1),
        "R", round(float(home["roll"]), 1),
        flush=True,
    )

    pick_and_place.interrupt(False)
    pick_pos = [float(x_m), float(y_m), PICK_HEIGHT_M]

    ok = pick_and_place.pick(
        pick_pos,
        90.0,
        float(roll),
        540,
        GRIPPER_DEPTH_M,
        pub,
        None,
        claw_grab_angle=CLAW_STRAWBERRY,
    )
    if not ok:
        print("CAPSUNA_V2_PICK_FAILED", flush=True)
        return

    ok = pick_and_place.place(
        list(DESTINATION_M),
        DESTINATION_PITCH,
        DESTINATION_ROLL,
        200,
        pub,
        None,
        claw_hold_angle=CLAW_STRAWBERRY,
    )
    if not ok:
        print("CAPSUNA_V2_PLACE_FAILED", flush=True)
        return

    print("CAPSUNA_V2_GATA", flush=True)


def finalize():
    global done
    done = True

    print("==============================", flush=True)
    print(f"CAPSUNA V2 HITS = {len(hits)}/{frames}", flush=True)
    print(f"STABLE COUNT = {stable_count}/{STABLE_REQUIRED}", flush=True)

    if len(hits) < MIN_FINAL_HITS:
        print("CAPSUNA_V2_ABORT_PREA_PUTINE_DETECTII", flush=True)
        return

    selected_hits = hits[-(STABLE_REQUIRED + 1):] if stable_count >= STABLE_REQUIRED else hits

    xs = np.array([h["pos"][0] for h in selected_hits], dtype=np.float64)
    ys = np.array([h["pos"][1] for h in selected_hits], dtype=np.float64)
    gx = int(np.median([h["gp"][0] for h in selected_hits]))
    gy = int(np.median([h["gp"][1] for h in selected_hits]))
    rx = int(np.median([h["rp"][0] for h in selected_hits]))
    ry = int(np.median([h["rp"][1] for h in selected_hits]))

    x_m = float(np.median(xs))
    y_m = float(np.median(ys))

    # Proven strawberry orientation calibration from BACKUP-005.
    stem_angle = math.degrees(math.atan2(gy - ry, gx - rx))
    roll = 80.0 + (stem_angle - 160.2)
    while roll > 90.0:
        roll -= 180.0
    while roll < -90.0:
        roll += 180.0

    # Pick the observation closest to the median stem position for collision geometry.
    representative = min(
        selected_hits,
        key=lambda h: math.hypot(h["gp"][0] - gx, h["gp"][1] - gy),
    )

    spread_mm = max(
        math.hypot((h["pos"][0] - x_m) * 1000.0, (h["pos"][1] - y_m) * 1000.0)
        for h in selected_hits
    )
    print("CODITA FINAL =", gx, gy, flush=True)
    print("PICK XY =", round(x_m * 1000.0, 1), round(y_m * 1000.0, 1), "mm", flush=True)
    print("SPREAD MAX =", round(spread_mm, 1), "mm", flush=True)
    print("STEM ANGLE =", round(stem_angle, 1), "ROLL =", round(roll, 1), flush=True)
    print("==============================", flush=True)

    # Hiwonder-inspired safety: if the target never became stable, only continue when
    # the median cloud is still compact enough to be equivalent to their 10 mm check.
    if stable_count < STABLE_REQUIRED and spread_mm > 10.0:
        print("CAPSUNA_V2_ABORT_INSTABILA", flush=True)
        return

    execute_pick(
        {
            "stem_px": (gx, gy),
            "xy_m": (x_m, y_m),
            "roll": roll,
            "stem_angle": stem_angle,
            "projection": representative["projection"],
            "red_rects": representative["red_rects"],
            "target_rp": representative["rp"],
        }
    )


def callback(msg):
    global frames, done, stable_count, previous_pos, previous_angle
    if done:
        return

    frames += 1
    bgr = np.frombuffer(msg.data, np.uint8).reshape(msg.height, msg.width, 3).copy()
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    r1 = cv2.inRange(hsv, np.array([0, 90, 50]), np.array([12, 255, 255]))
    r2 = cv2.inRange(hsv, np.array([170, 90, 50]), np.array([179, 255, 255]))
    red = cv2.bitwise_or(r1, r2)
    green = cv2.inRange(hsv, np.array([35, 50, 35]), np.array([90, 255, 255]))

    rc = contours(red, 300)
    gc = contours(green, 5)
    pair = pair_target(rc, gc)

    if pair is None:
        stable_count = 0
        previous_pos = None
        previous_angle = None
        print("FRAME", frames, "FARA PERECHE", flush=True)
    else:
        _, _, target_red, rp, _, gp, distance_px = pair
        stem_angle = math.degrees(math.atan2(gp[1] - rp[1], gp[0] - rp[0]))
        pos, projection = calibrated_pose.pixel_to_calibrated_world(
            gp, K, (tvec, rmat), W, CAL, height=PICK_HEIGHT_M
        )
        pos = np.asarray(pos, dtype=np.float64)

        if previous_pos is not None and previous_angle is not None:
            position_difference = float(np.linalg.norm(pos - previous_pos))
            if position_difference < STABLE_POSITION_M and angle_diff(stem_angle, previous_angle) < STABLE_ANGLE_DEG:
                stable_count += 1
            else:
                stable_count = 0
        else:
            stable_count = 0

        previous_pos = pos.copy()
        previous_angle = stem_angle

        red_rects = [cv2.minAreaRect(c) for c in rc]
        target_rect = cv2.minAreaRect(target_red)
        hits.append(
            {
                "rp": rp,
                "gp": gp,
                "distance": distance_px,
                "pos": pos,
                "angle": stem_angle,
                "projection": projection,
                "red_rects": red_rects,
                "target_rect": target_rect,
            }
        )
        print(
            "FRAME", frames,
            "ROSU", rp,
            "CODITA", gp,
            "DIST", round(distance_px, 1),
            "STABLE", stable_count,
            flush=True,
        )

    if stable_count >= STABLE_REQUIRED or frames >= MAX_FRAMES:
        finalize()


node.create_subscription(Image, "/aurora/rgb/image_raw", callback, 10)

while rclpy.ok() and not done:
    rclpy.spin_once(node, timeout_sec=2.0)

node.destroy_node()
rclpy.shutdown()
