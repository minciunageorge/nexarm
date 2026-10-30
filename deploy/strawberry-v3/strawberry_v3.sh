#!/bin/bash
set -u
LOG=/home/pi/nexarm-buttons/strawberry-v3-last.log
{
  echo "=== CAPSUNA V3 $(date -Is) ==="
  docker cp /home/pi/nexarm-buttons/strawberry_v3.py nexarm:/tmp/strawberry_v3.py >/dev/null
  docker exec -e CAPSUNA_V3_MOVE=1 nexarm bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; source /home/ubuntu/third_party_ros2/third_party_ws/install/setup.bash; python3 /tmp/strawberry_v3.py'
} >"$LOG" 2>&1
