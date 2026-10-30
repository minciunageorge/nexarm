#!/bin/bash
set -u
LOG=/home/pi/nexarm-buttons/strawberry-v4-last.log
LOCK=/tmp/capsuna-v4.lock

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "=== CAPSUNA V4 $(date -Is) ===" >"$LOG"
  echo "CAPSUNA_V4_DEJA_RULEAZA" >>"$LOG"
  exit 0
fi

{
  echo "=== CAPSUNA V4 $(date -Is) ==="
  docker cp /home/pi/nexarm-buttons/strawberry_v4.py nexarm:/tmp/strawberry_v4.py >/dev/null
  docker exec -e CAPSUNA_V4_MOVE=1 nexarm bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; source /home/ubuntu/third_party_ros2/third_party_ws/install/setup.bash; python3 /tmp/strawberry_v4.py'
} >"$LOG" 2>&1
