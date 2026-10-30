#!/bin/bash
set -euo pipefail

CONTAINER=nexarm
LOG=/home/pi/nexarm-buttons/mode-switch.log
LOCK=/tmp/nexarm-mode.lock
CUSTOM_CAM='^/usr/bin/python3 /opt/ros/humble/bin/ros2 launch deptrum-ros-driver-aurora930 aurora930_launch.py$'
SAVED_CMD=/tmp/nexarm-factory-camera.cmdline
CUSTOM_LOG=/tmp/nexarm-custom-camera-root.log

mkdir -p "$(dirname "$LOG")"
exec 9>"$LOCK"
flock -n 9 || { echo "mode switch already running" | tee -a "$LOG"; exit 1; }
exec >>"$LOG" 2>&1
printf '\n[%s] %s\n' "$(date -Is)" "$*"

custom_launch_present() {
  docker exec "$CONTAINER" pgrep -u root -f "$CUSTOM_CAM" >/dev/null 2>&1
}

factory_camera_pid() {
  docker exec "$CONTAINER" bash -lc 'for p in $(pgrep -u ubuntu -x aurora930_node 2>/dev/null || true); do c=$(tr "\0" " " < /proc/$p/cmdline 2>/dev/null || true); case "$c" in *"/depth_cam/rgb/image_raw"*) echo "$p"; exit 0;; esac; done; exit 1'
}

custom_camera_child_present() {
  docker exec "$CONTAINER" pgrep -u root -x aurora930_node >/dev/null 2>&1
}

save_and_stop_factory_camera() {
  local pid
  pid="$(factory_camera_pid)" || { echo FACTORY_CAMERA_NOT_FOUND; return 1; }
  docker exec "$CONTAINER" bash -lc "cat /proc/$pid/cmdline > $SAVED_CMD"
  docker exec "$CONTAINER" kill -INT "$pid"
  for ((i=0;i<40;i++)); do
    docker exec "$CONTAINER" test -e "/proc/$pid" || { echo FACTORY_CAMERA_STOPPED; return 0; }
    sleep 0.25
  done
  docker exec "$CONTAINER" kill -TERM "$pid" 2>/dev/null || true
  sleep 1
  docker exec "$CONTAINER" test ! -e "/proc/$pid" || { echo FACTORY_CAMERA_DID_NOT_STOP; return 1; }
  echo FACTORY_CAMERA_STOPPED_AFTER_TERM
}

stop_custom_camera() {
  docker exec "$CONTAINER" bash -lc "p=\$(pgrep -u root -f '$CUSTOM_CAM' || true); [ -z \"\$p\" ] || kill -INT \$p" || true
  sleep 2
  docker exec "$CONTAINER" bash -lc 'for p in $(pgrep -u root -x aurora930_node 2>/dev/null || true); do kill -INT "$p" 2>/dev/null || true; done' || true
  for ((i=0;i<40;i++)); do
    if ! custom_launch_present && ! custom_camera_child_present; then
      echo CUSTOM_CAMERA_STOPPED
      return 0
    fi
    sleep 0.25
  done
  docker exec "$CONTAINER" bash -lc 'for p in $(pgrep -u root -x aurora930_node 2>/dev/null || true); do kill -TERM "$p" 2>/dev/null || true; done' || true
  sleep 1
  ! custom_camera_child_present
}

restore_factory_camera() {
  if factory_camera_pid >/dev/null 2>&1; then
    echo FACTORY_CAMERA_ALREADY_RUNNING
    return 0
  fi
  docker exec "$CONTAINER" test -s "$SAVED_CMD" || { echo NO_SAVED_FACTORY_CAMERA_COMMAND; return 1; }
  docker exec -d --user ubuntu "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/third_party_ros2/third_party_ws/install/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; python3 -c '\''import os; raw=open("/tmp/nexarm-factory-camera.cmdline","rb").read().split(b"\0"); a=[x.decode() for x in raw if x]; os.execv(a[0],a)'\'' >>/tmp/nexarm-factory-camera-restored.log 2>&1'
  for ((i=0;i<60;i++)); do
    if factory_camera_pid >/dev/null 2>&1; then
      echo FACTORY_CAMERA_RESTORED
      return 0
    fi
    sleep 0.25
  done
  echo FACTORY_CAMERA_RESTORE_FAILED
  return 1
}

camera_has_frames() {
  docker exec "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/third_party_ros2/third_party_ws/install/setup.bash; timeout 6 ros2 topic hz /aurora/rgb/image_raw 2>&1 | grep -m1 -q "average rate"'
}

controller_topic_present() {
  docker exec "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; ros2 topic list 2>/dev/null | grep -qx "/ros_robot_controller/arm/set_coords"' >/dev/null 2>&1
}

start_custom() {
  if custom_launch_present && custom_camera_child_present && camera_has_frames; then
    echo CUSTOM_ALREADY_READY
    return 0
  fi

  stop_custom_camera || true
  if ! factory_camera_pid >/dev/null 2>&1; then
    echo FACTORY_CAMERA_MISSING_REFUSING_SWITCH
    return 1
  fi
  save_and_stop_factory_camera || return 1

  sleep 2
  docker exec -d "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/third_party_ros2/third_party_ws/install/setup.bash; ros2 launch deptrum-ros-driver-aurora930 aurora930_launch.py >/tmp/nexarm-custom-camera-root.log 2>&1'

  sleep 4
  if custom_launch_present && custom_camera_child_present && camera_has_frames; then
    echo CUSTOM_READY_CAMERA_14FPS
    return 0
  fi

  echo CUSTOM_VERIFY_FAILED_RESTORING_FACTORY_CAMERA
  docker exec "$CONTAINER" bash -lc 'ps -eo pid,ppid,user,stat,args | grep -E "aurora930_node|ros2 launch deptrum" | grep -v grep || true; tail -n 100 /tmp/nexarm-custom-camera-root.log 2>/dev/null || true'
  stop_custom_camera || true
  sleep 1
  restore_factory_camera || true
  return 1
}

return_factory() {
  stop_custom_camera || return 1
  sleep 1
  restore_factory_camera
}

status() {
  local f=0 c=0 ctrl=0 bringup=0
  factory_camera_pid >/dev/null 2>&1 && f=1 || true
  { custom_launch_present || custom_camera_child_present; } && c=1 || true
  controller_topic_present && ctrl=1 || true
  docker exec "$CONTAINER" pgrep -u ubuntu -f '^/usr/bin/python3 /opt/ros/humble/bin/ros2 launch bringup bringup.launch.py$' >/dev/null 2>&1 && bringup=1 || true
  echo "factory_camera=$f custom_camera=$c controller_topic=$ctrl bringup=$bringup"
  if ((f==1 && c==0)); then echo MODE=FACTORY; return 0; fi
  if ((f==0 && c==1)); then echo MODE=CUSTOM; return 0; fi
  echo MODE=INCOMPLETE
  return 0
}

case "${1:-status}" in
  start|custom) start_custom ;;
  stop|factory) return_factory ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|status}"; exit 2 ;;
esac
