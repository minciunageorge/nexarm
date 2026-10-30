#!/bin/bash
set -euo pipefail

CONTAINER=nexarm
LOG=/home/pi/nexarm-buttons/mode-switch.log
LOCK=/tmp/nexarm-mode.lock
FACTORY='^/usr/bin/python3 /opt/ros/humble/bin/ros2 launch bringup bringup.launch.py$'
CUSTOM_CAM='^/usr/bin/python3 /opt/ros/humble/bin/ros2 launch deptrum-ros-driver-aurora930 aurora930_launch.py$'
CUSTOM_CTRL='^/usr/bin/python3 /opt/ros/humble/bin/ros2 launch sdk nexarm.launch.py$'
CAM_NODE='aurora930_node'
CTRL_NODE='ros_robot_controller'
SERVO_NODE='servo_controller'

mkdir -p "$(dirname "$LOG")"
exec 9>"$LOCK"
flock -n 9 || { echo "mode switch already running" | tee -a "$LOG"; exit 1; }
exec >>"$LOG" 2>&1
printf '\n[%s] %s\n' "$(date -Is)" "$*"

pgrep_exact() {
  docker exec "$CONTAINER" pgrep -u ubuntu -f "$1" >/dev/null 2>&1
}

signal_exact() {
  local sig="$1" pattern="$2"
  docker exec "$CONTAINER" bash -lc "p=\$(pgrep -u ubuntu -f '$pattern' || true); [ -z \"\$p\" ] || kill -$sig \$p"
}

wait_gone() {
  local pattern="$1" limit="${2:-40}"
  for ((i=0;i<limit;i++)); do
    pgrep_exact "$pattern" || return 0
    sleep 0.25
  done
  return 1
}

known_factory_children_present() {
  docker exec "$CONTAINER" bash -lc "pgrep -u ubuntu -f 'aurora930_node|ros_robot_controller|servo_controller|lab_manager|finger_trace|object_tracking|object_sorting|shape_recognition|yolo_node|waste_classification|web_video_server|rosbridge|scene5_peer_agent' >/dev/null 2>&1"
}

start_factory() {
  if pgrep_exact "$FACTORY"; then
    echo FACTORY_ALREADY_RUNNING
    return 0
  fi
  docker exec -d --user ubuntu "$CONTAINER" bash -lc 'export CAMERA_TYPE=aurora CHASSIS_TYPE=None; source /opt/ros/humble/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; exec /opt/ros/humble/bin/ros2 launch bringup bringup.launch.py >>/tmp/nexarm-factory-restore.log 2>&1'
  for ((i=0;i<60;i++)); do
    if pgrep_exact "$FACTORY"; then
      echo FACTORY_STARTED
      return 0
    fi
    sleep 0.25
  done
  echo FACTORY_START_FAILED
  return 1
}

stop_custom() {
  signal_exact INT "$CUSTOM_CAM"
  signal_exact INT "$CUSTOM_CTRL"
  wait_gone "$CUSTOM_CAM" 40 || true
  wait_gone "$CUSTOM_CTRL" 40 || true
  sleep 1
  if docker exec "$CONTAINER" pgrep -u ubuntu -f "$CAM_NODE|$CTRL_NODE|$SERVO_NODE" >/dev/null 2>&1; then
    echo CUSTOM_CHILD_REMAINS
    return 1
  fi
}

verify_custom() {
  docker exec "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/third_party_ros2/third_party_ws/install/setup.bash; timeout 8 ros2 topic hz /aurora/rgb/image_raw 2>&1 | grep -m1 -q "average rate"' || return 1
  docker exec "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; ros2 topic list | grep -qx "/ros_robot_controller/arm/set_coords"' || return 1
}

start_custom() {
  if pgrep_exact "$CUSTOM_CAM" && pgrep_exact "$CUSTOM_CTRL" && ! pgrep_exact "$FACTORY"; then
    verify_custom && { echo CUSTOM_ALREADY_RUNNING; return 0; }
  fi

  stop_custom || true

  if pgrep_exact "$FACTORY"; then
    signal_exact INT "$FACTORY"
    if ! wait_gone "$FACTORY" 60; then
      echo FACTORY_DID_NOT_STOP
      return 1
    fi
    sleep 2
  fi

  if known_factory_children_present; then
    echo FACTORY_CHILDREN_REMAIN_ABORTING_CUSTOM
    start_factory || true
    return 1
  fi

  docker exec -d --user ubuntu "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/third_party_ros2/third_party_ws/install/setup.bash; exec /opt/ros/humble/bin/ros2 launch deptrum-ros-driver-aurora930 aurora930_launch.py >>/tmp/nexarm-custom-camera.log 2>&1'
  docker exec -d --user ubuntu "$CONTAINER" bash -lc 'export CHASSIS_TYPE=None; source /opt/ros/humble/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; exec /opt/ros/humble/bin/ros2 launch sdk nexarm.launch.py >>/tmp/nexarm-custom-controller.log 2>&1'

  for ((i=0;i<80;i++)); do
    if pgrep_exact "$CUSTOM_CAM" && pgrep_exact "$CUSTOM_CTRL"; then
      if verify_custom; then
        echo CUSTOM_READY
        return 0
      fi
    fi
    sleep 0.25
  done

  echo CUSTOM_VERIFY_FAILED_ROLLING_BACK
  stop_custom || true
  start_factory || true
  return 1
}

status() {
  local f=0 c=0 r=0
  pgrep_exact "$FACTORY" && f=1 || true
  pgrep_exact "$CUSTOM_CAM" && c=1 || true
  pgrep_exact "$CUSTOM_CTRL" && r=1 || true
  echo "factory=$f custom_camera=$c custom_controller=$r"
  if ((f==1 && c==0 && r==0)); then echo MODE=FACTORY; return 0; fi
  if ((f==0 && c==1 && r==1)); then echo MODE=CUSTOM; return 0; fi
  echo MODE=MIXED_OR_STOPPED
  return 2
}

case "${1:-status}" in
  start|custom) start_custom ;;
  stop|factory) stop_custom && start_factory ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|status}"; exit 2 ;;
esac
