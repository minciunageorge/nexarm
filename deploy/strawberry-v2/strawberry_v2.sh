#!/bin/bash
set -u
LOG=/home/pi/nexarm-buttons/strawberry-v2-last.log
{
  echo "=== CAPSUNA V2 $(date -Is) ==="
  docker cp /home/pi/nexarm-buttons/strawberry_v2.py nexarm:/tmp/strawberry_v2.py >/dev/null
  docker exec nexarm bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; python3 /tmp/strawberry_v2.py'
} >"$LOG" 2>&1
