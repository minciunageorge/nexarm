#!/usr/bin/env python3
from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding='utf-8')

# Keep the proven V3 green range for a normal strawberry stem, and add a
# separate weak-green path for a sub-pixel painted wire.
repls = {
    'MIN_LINE_HITS = 5': 'MIN_LINE_HITS = 4',
    'CENTER_TOL_PX = 12.0': 'CENTER_TOL_PX = 28.0',
    'ANGLE_TOL_DEG = 12.0': 'ANGLE_TOL_DEG = 14.0',
    'GREEN_LOW = np.array([30, 35, 25], dtype=np.uint8)': 'GREEN_LOW = np.array([35, 50, 35], dtype=np.uint8)',
    'GREEN_HIGH = np.array([95, 255, 255], dtype=np.uint8)': 'GREEN_HIGH = np.array([90, 255, 255], dtype=np.uint8)',
}
for old, new in repls.items():
    if old not in s:
        raise SystemExit(f'missing expected text: {old}')
    s = s.replace(old, new, 1)

needle = 'MIN_GREEN_SUPPORT = 0.25\n'
extra = '''MIN_GREEN_SUPPORT = 0.25
WIRE_EDGE_MARGIN = 18
WIRE_RHO_TOL_PX = 16.0
WIRE_CLUSTER_ANGLE_DEG = 14.0
MAX_CANDIDATES_PER_FRAME = 10
WEAK_GREEN_LOW = np.array([25, 20, 15], dtype=np.uint8)
WEAK_GREEN_HIGH = np.array([105, 255, 255], dtype=np.uint8)
'''
if needle not in s:
    raise SystemExit('missing MIN_GREEN_SUPPORT constant')
s = s.replace(needle, extra, 1)

start = s.index('    def detect_green_wire(self, bgr):\n')
end = s.index('    def depth_roi(self, depth, x, y):\n', start)
new_detect = '''    def detect_green_wire(self, bgr):
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

        # Strong green = the exact family that already worked on the V1/V3 stem.
        strong = cv2.inRange(hsv, GREEN_LOW, GREEN_HIGH)

        # Weak green = for a ~0.5 mm painted wire whose pixels are mixed with
        # the background. It must still be genuinely green-dominant.
        weak_hsv = cv2.inRange(hsv, WEAK_GREEN_LOW, WEAK_GREEN_HIGH)
        b = bgr[:, :, 0].astype(np.int16)
        g = bgr[:, :, 1].astype(np.int16)
        r = bgr[:, :, 2].astype(np.int16)
        dominant = ((g > 24) & ((g - r) >= 6) & ((g - b) >= 4)).astype(np.uint8) * 255
        weak = cv2.bitwise_and(weak_hsv, dominant)
        raw = cv2.bitwise_or(strong, weak)

        # Camera/image borders were the source of the 400-640 px false lines.
        m = WIRE_EDGE_MARGIN
        raw[:m, :] = 0
        raw[-m:, :] = 0
        raw[:, :m] = 0
        raw[:, -m:] = 0
        strong[:m, :] = 0
        strong[-m:, :] = 0
        strong[:, :m] = 0
        strong[:, -m:] = 0
        weak[:m, :] = 0
        weak[-m:, :] = 0
        weak[:, :m] = 0
        weak[:, -m:] = 0

        # Contour + fitted line is far cheaper and more stable here than a very
        # permissive Hough transform over the whole image.
        connected = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=1)
        thick = cv2.dilate(connected, np.ones((3, 3), np.uint8), iterations=1)
        cs = cv2.findContours(thick, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[-2]
        h, w = raw.shape[:2]
        candidates = []

        for c in cs:
            if len(c) < 2:
                continue
            pts = c.reshape(-1, 2).astype(np.float64)
            vx, vy, x0, y0 = [float(v) for v in cv2.fitLine(c, cv2.DIST_L2, 0, 0.01, 0.01).reshape(-1)]
            norm = math.hypot(vx, vy)
            if norm < 1e-6:
                continue
            vx /= norm
            vy /= norm
            if vx < 0 or (abs(vx) < 1e-9 and vy < 0):
                vx = -vx
                vy = -vy

            relx = pts[:, 0] - x0
            rely = pts[:, 1] - y0
            t = relx * vx + rely * vy
            t0 = float(np.min(t)); t1 = float(np.max(t))
            length = t1 - t0
            if length < 5.0:
                continue

            nx, ny = -vy, vx
            perp = np.abs(relx * nx + rely * ny)
            thickness = 2.0 * float(np.percentile(perp, 90)) + 1.0
            # Reject leaves/blobs and broad background regions; retain thin stems/wires.
            if thickness > max(18.0, length * 0.35):
                continue

            x1 = x0 + vx * t0; y1 = y0 + vy * t0
            x2 = x0 + vx * t1; y2 = y0 + vy * t1
            cx = (x1 + x2) / 2.0; cy = (y1 + y2) / 2.0
            if cx < m or cx >= w - m or cy < m or cy >= h - m:
                continue

            # A line spanning almost the full image is an image/background edge,
            # not the small target we are trying to grasp.
            if length > 0.78 * max(w, h):
                continue

            samples = min(120, max(10, int(length)))
            xs = np.clip(np.rint(np.linspace(x1, x2, samples)).astype(int), 0, w - 1)
            ys = np.clip(np.rint(np.linspace(y1, y2, samples)).astype(int), 0, h - 1)
            strong_hits = 0
            weak_hits = 0
            for x, y in zip(xs, ys):
                yy0, yy1 = max(0, y - 1), min(h, y + 2)
                xx0, xx1 = max(0, x - 1), min(w, x + 2)
                if np.any(strong[yy0:yy1, xx0:xx1] > 0):
                    strong_hits += 1
                if np.any(weak[yy0:yy1, xx0:xx1] > 0):
                    weak_hits += 1
            strong_ratio = strong_hits / float(samples)
            weak_ratio = weak_hits / float(samples)
            if strong_ratio < 0.10 and weak_ratio < 0.22:
                continue

            angle = math.degrees(math.atan2(vy, vx))
            while angle >= 90.0:
                angle -= 180.0
            while angle < -90.0:
                angle += 180.0
            theta = math.radians(angle)
            rho = (-math.sin(theta)) * cx + math.cos(theta) * cy

            # Strong V3-like green is deliberately worth more than weak green.
            score = strong_ratio * 4.0 + weak_ratio * 2.0 + min(length, 120.0) / 120.0 - min(thickness, 18.0) / 36.0
            candidates.append({
                'center': (float(cx), float(cy)),
                'angle': float(angle),
                'length': float(length),
                'thickness': float(thickness),
                'strong': float(strong_ratio),
                'weak': float(weak_ratio),
                'rho': float(rho),
                'score': float(score),
                'segment': (int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))),
            })

        candidates.sort(key=lambda d: d['score'], reverse=True)
        return candidates[:MAX_CANDIDATES_PER_FRAME]

'''
s = s[:start] + new_detect + s[end:]

start = s.index('    def collect_level(self, z_mm):\n')
end = s.index('    def target_world(self, detection):\n', start)
new_collect = '''    def collect_level(self, z_mm):
        observations = []
        seen_seq = -1
        deadline = time.time() + 2.8
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

            candidates = self.detect_green_wire(bgr)
            if not candidates:
                print('V4 Z', int(z_mm), 'FRAME', fresh, 'FARA FIR', flush=True)
                continue

            for d0 in candidates:
                d = dict(d0)
                depth_mm, valid_n = self.depth_roi(depth, d['center'][0], d['center'][1])
                d['depth_mm'] = depth_mm
                d['valid_depth'] = valid_n
                d['k'] = k
                d['frame'] = fresh
                observations.append(d)

            best = candidates[0]
            print(
                'V4 Z', int(z_mm), 'FRAME', fresh,
                'CAND', len(candidates),
                'BEST', round(best['center'][0], 1), round(best['center'][1], 1),
                'ANGLE', round(best['angle'], 1),
                'LEN', round(best['length'], 1),
                'TH', round(best['thickness'], 1),
                'STR', round(best['strong'], 2),
                'WEAK', round(best['weak'], 2),
                flush=True,
            )

        if not observations:
            return None

        # Build temporal consensus by infinite-line geometry: same angle and same
        # perpendicular offset (rho). This is much more stable than comparing the
        # midpoint of Hough segments, which can slide along a long wire.
        best_cluster = None
        best_cluster_score = None
        for seed in observations:
            a = math.radians(seed['angle'])
            nx = -math.sin(a); ny = math.cos(a)
            seed_rho = nx * seed['center'][0] + ny * seed['center'][1]
            matches = []
            for d in observations:
                if angle_diff_180(d['angle'], seed['angle']) > WIRE_CLUSTER_ANGLE_DEG:
                    continue
                rho_on_seed = nx * d['center'][0] + ny * d['center'][1]
                if abs(rho_on_seed - seed_rho) <= WIRE_RHO_TOL_PX:
                    matches.append(d)

            # At most one observation per camera frame counts toward consensus.
            by_frame = {}
            for d in matches:
                f = d['frame']
                if f not in by_frame or d['score'] > by_frame[f]['score']:
                    by_frame[f] = d
            cluster = list(by_frame.values())
            if len(cluster) < MIN_LINE_HITS:
                continue

            cx = float(np.median([d['center'][0] for d in cluster]))
            cy = float(np.median([d['center'][1] for d in cluster]))
            strong_med = float(np.median([d['strong'] for d in cluster]))
            weak_med = float(np.median([d['weak'] for d in cluster]))
            length_med = float(np.median([d['length'] for d in cluster]))
            center_penalty = math.hypot(cx - 320.0, cy - 200.0) / 80.0
            score = len(cluster) * 100.0 + strong_med * 40.0 + weak_med * 15.0 + min(length_med, 150.0) / 10.0 - center_penalty
            if best_cluster_score is None or score > best_cluster_score:
                best_cluster_score = score
                best_cluster = cluster

        if best_cluster is None:
            frame_count = len(set(d['frame'] for d in observations))
            print('V4 FIRURI VAZUTE DAR FARA CONSENSUS', len(observations), 'CANDIDATI DIN', frame_count, 'CADRE', flush=True)
            return None

        cx = float(np.median([d['center'][0] for d in best_cluster]))
        cy = float(np.median([d['center'][1] for d in best_cluster]))
        angle = median_angle_180([d['angle'] for d in best_cluster])
        depth_values = [d['depth_mm'] for d in best_cluster if d['depth_mm'] is not None]
        depth_mm = float(np.median(depth_values)) if depth_values else None
        length = float(np.median([d['length'] for d in best_cluster]))
        spread = max(math.hypot(d['center'][0] - cx, d['center'][1] - cy) for d in best_cluster)
        strong_med = float(np.median([d['strong'] for d in best_cluster]))
        weak_med = float(np.median([d['weak'] for d in best_cluster]))

        print(
            'V4 CONSENSUS', len(best_cluster), 'CADRE',
            'CENTER', round(cx, 1), round(cy, 1),
            'ANGLE', round(angle, 1),
            'STR', round(strong_med, 2),
            'WEAK', round(weak_med, 2),
            'DEPTH', None if depth_mm is None else round(depth_mm, 1),
            flush=True,
        )

        return {
            'center': (cx, cy),
            'angle': angle,
            'length': length,
            'depth_mm': depth_mm,
            'k': best_cluster[-1]['k'],
            'hits': len(best_cluster),
            'spread_px': spread,
        }

'''
s = s[:start] + new_collect + s[end:]

p.write_text(s, encoding='utf-8')
print('CAPSUNA_V4_TEMPORAL_GREEN_CONSENSUS_V2')
