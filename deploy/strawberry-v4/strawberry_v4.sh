#!/bin/bash
set -u
LOG=/home/pi/nexarm-buttons/strawberry-v4-last.log

if command -v zenity >/dev/null 2>&1; then
  zenity --question --title="CAPSUNA V4" --text="V4 va cobori bratul din HOME in pasi de 10 mm pana gaseste firul verde, apoi revine HOME daca nu il gaseste. Continui?" || exit 0
fi

{
  echo "=== CAPSUNA V4 $(date -Is) ==="
  docker cp /home/pi/nexarm-buttons/strawberry_v4.py nexarm:/tmp/strawberry_v4.py >/dev/null
  docker exec -e CAPSUNA_V4_MOVE=1 nexarm bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; source /home/ubuntu/third_party_ros2/third_party_ws/install/setup.bash; python3 /tmp/strawberry_v4.py'
} >"$LOG" 2>&1
