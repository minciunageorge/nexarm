#!/usr/bin/env python3
from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding='utf-8')

repls = {
    'MIN_LINE_HITS = 5': 'MIN_LINE_HITS = 3',
    'CENTER_TOL_PX = 12.0': 'CENTER_TOL_PX = 18.0',
    'ANGLE_TOL_DEG = 12.0': 'ANGLE_TOL_DEG = 18.0',
    'GREEN_LOW = np.array([30, 35, 25], dtype=np.uint8)': 'GREEN_LOW = np.array([20, 10, 10], dtype=np.uint8)',
    'GREEN_HIGH = np.array([95, 255, 255], dtype=np.uint8)': 'GREEN_HIGH = np.array([105, 255, 255], dtype=np.uint8)',
    'HOUGH_THRESHOLD = 6': 'HOUGH_THRESHOLD = 3',
    'MIN_LINE_LENGTH_PX = 8': 'MIN_LINE_LENGTH_PX = 4',
    'MAX_LINE_GAP_PX = 14': 'MAX_LINE_GAP_PX = 24',
    'MIN_GREEN_SUPPORT = 0.25': 'MIN_GREEN_SUPPORT = 0.08',
    'thick = cv2.dilate(connected, np.ones((3, 3), np.uint8), iterations=1)': 'thick = cv2.dilate(connected, np.ones((3, 3), np.uint8), iterations=2)',
}
for old, new in repls.items():
    if old not in s:
        raise SystemExit(f'missing expected text: {old}')
    s = s.replace(old, new, 1)

needle = '        raw = cv2.inRange(hsv, GREEN_LOW, GREEN_HIGH)\n\n'
insert = '''        raw = cv2.inRange(hsv, GREEN_LOW, GREEN_HIGH)\n\n        # Extra detector for a very thin green painted wire. At sub-pixel width,\n        # the RGB pixel is mixed with the background and saturation can fall sharply.\n        b = bgr[:, :, 0].astype(np.int16)\n        g = bgr[:, :, 1].astype(np.int16)\n        r = bgr[:, :, 2].astype(np.int16)\n        green_dominant = ((g > 22) & ((g - r) >= 3) & ((g - b) >= 1)).astype(np.uint8) * 255\n        raw = cv2.bitwise_or(raw, green_dominant)\n\n'''
if needle not in s:
    raise SystemExit('missing raw-mask insertion point')
s = s.replace(needle, insert, 1)

p.write_text(s, encoding='utf-8')
print('CAPSUNA_V4_DETECTOR_PATCHED')
