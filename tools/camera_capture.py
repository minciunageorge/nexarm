#!/usr/bin/env python3
"""Capture one raw ROS Image without starting/configuring a camera driver."""
import argparse
import json
from pathlib import Path
import time


def ppm_bytes(width, height, encoding, step, data):
    if width <= 0 or height <= 0 or width * height > 20_000_000:
        raise ValueError('Invalid or oversized image')
    channels = {'rgb8': 3, 'bgr8': 3, 'mono8': 1}.get(encoding)
    if channels is None:
        raise ValueError(f'Unsupported encoding {encoding}; use rgb8/bgr8/mono8')
    if step < width * channels or len(data) < step * height:
        raise ValueError('Truncated image or invalid row stride')
    pixels = bytearray()
    for y in range(height):
        row = data[y * step:y * step + width * channels]
        if encoding == 'mono8':
            for value in row:
                pixels.extend((value, value, value))
        elif encoding == 'bgr8':
            for x in range(0, len(row), 3):
                pixels.extend((row[x + 2], row[x + 1], row[x]))
        else:
            pixels.extend(row)
    return f'P6\n{width} {height}\n255\n'.encode() + pixels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--topic', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=20)
    args = parser.parse_args()
    if not 0 < args.timeout <= 300:
        parser.error('Timeout must be between 0 and 300 seconds')
    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import Image
    rclpy.init()
    node = rclpy.create_node('nexarm_camera_capture')
    received = []
    subscription = node.create_subscription(Image, args.topic, received.append, qos_profile_sensor_data)
    try:
        deadline = time.monotonic() + args.timeout
        while not received and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=min(0.2, max(0, deadline - time.monotonic())))
        if not received:
            raise TimeoutError(f'No image on {args.topic} within {args.timeout}s')
        msg = received[0]
        image = ppm_bytes(msg.width, msg.height, msg.encoding, msg.step, bytes(msg.data))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.output.with_suffix('.tmp')
        temp.write_bytes(image)
        temp.replace(args.output)
        args.output.with_suffix('.json').write_text(json.dumps({
            'topic': args.topic, 'width': msg.width, 'height': msg.height,
            'encoding': msg.encoding, 'frame_id': msg.header.frame_id,
            'stamp_sec': msg.header.stamp.sec, 'stamp_nanosec': msg.header.stamp.nanosec,
        }, indent=2) + '\n')
        print(f'Captured {msg.width}x{msg.height} to {args.output}')
    finally:
        node.destroy_subscription(subscription)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
