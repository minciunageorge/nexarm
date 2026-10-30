#!/bin/bash
set -euo pipefail

CONTAINER=nexarm
LOG=/home/pi/nexarm-buttons/mode-switch.log
LOCK=/tmp/nexarm-mode.lock
CUSTOM_CAM='^/usr/bin/python3 /opt/ros/humble/bin/ros2 launch deptrum-ros-driver-aurora930 aurora930_launch.py$'
CUSTOM_CTRL='^/usr/bin/python3 /opt/ros/humble/bin/ros2 launch sdk nexarm.launch.py$'
SAVED_CAM_CMD=/tmp/nexarm-factory-camera.cmdline
SAVED_CTRL_CMD=/tmp/nexarm-factory-controller.cmdline
SAVED_SERVO_CMD=/tmp/nexarm-factory-servo.cmdline

mkdir -p "$(dirname "$LOG")"
exec 9>"$LOCK"
flock -n 9 || { echo "mode switch already running" | tee -a "$LOG"; exit 1; }
exec >>"$LOG" 2>&1
printf '\n[%s] %s\n' "$(date -Is)" "$*"

custom_camera_launch_present() {
  docker exec "$CONTAINER" pgrep -u root -f "$CUSTOM_CAM" >/dev/null 2>&1
}

factory_camera_pid() {
  docker exec "$CONTAINER" bash -lc 'for p in $(pgrep -u ubuntu -x aurora930_node 2>/dev/null || true); do c=$(tr "\0" " " < /proc/$p/cmdline 2>/dev/null || true); case "$c" in *"/depth_cam/rgb/image_raw"*) echo "$p"; exit 0;; esac; done; exit 1'
}

custom_camera_child_present() {
  docker exec "$CONTAINER" pgrep -u root -x aurora930_node >/dev/null 2>&1
}

factory_controller_pid() {
  docker exec "$CONTAINER" pgrep -u ubuntu -f '/home/ubuntu/ros2_ws/install/ros_robot_controller/lib/ros_robot_controller/ros_robot_controller' | head -n1
}

factory_servo_pid() {
  docker exec "$CONTAINER" pgrep -u ubuntu -f '/home/ubuntu/ros2_ws/install/servo_controller/lib/servo_controller/servo_controller' | head -n1
}

custom_controller_launch_present() {
  docker exec "$CONTAINER" pgrep -u root -f "$CUSTOM_CTRL" >/dev/null 2>&1
}

custom_controller_children_present() {
  docker exec "$CONTAINER" pgrep -u root -f '/home/ubuntu/ros2_ws/install/ros_robot_controller/lib/ros_robot_controller/ros_robot_controller' >/dev/null 2>&1 && \
  docker exec "$CONTAINER" pgrep -u root -f '/home/ubuntu/ros2_ws/install/servo_controller/lib/servo_controller/servo_controller' >/dev/null 2>&1
}

camera_has_frames() {
  docker exec "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/third_party_ros2/third_party_ws/install/setup.bash; timeout 6 ros2 topic hz /aurora/rgb/image_raw 2>&1 | grep -m1 -q "average rate"'
}

save_and_stop_factory_camera() {
  local pid
  pid="$(factory_camera_pid)" || { echo FACTORY_CAMERA_NOT_FOUND; return 1; }
  docker exec "$CONTAINER" bash -lc "cat /proc/$pid/cmdline > $SAVED_CAM_CMD"
  docker exec "$CONTAINER" kill -INT "$pid" || true
  for ((i=0;i<40;i++)); do
    docker exec "$CONTAINER" test -e "/proc/$pid" || { echo FACTORY_CAMERA_STOPPED; return 0; }
    sleep 0.25
  done
  docker exec "$CONTAINER" kill -TERM "$pid" 2>/dev/null || true
  sleep 1
  docker exec "$CONTAINER" test ! -e "/proc/$pid" || { echo FACTORY_CAMERA_DID_NOT_STOP; return 1; }
  echo FACTORY_CAMERA_STOPPED_AFTER_TERM
}

save_and_stop_factory_controllers() {
  local cpid spid
  cpid="$(factory_controller_pid)" || { echo FACTORY_CONTROLLER_NOT_FOUND; return 1; }
  spid="$(factory_servo_pid)" || { echo FACTORY_SERVO_NOT_FOUND; return 1; }
  docker exec "$CONTAINER" bash -lc "cat /proc/$cpid/cmdline > $SAVED_CTRL_CMD; cat /proc/$spid/cmdline > $SAVED_SERVO_CMD"
  docker exec "$CONTAINER" kill -INT "$cpid" "$spid" 2>/dev/null || true
  for ((i=0;i<40;i++)); do
    if ! docker exec "$CONTAINER" test -e "/proc/$cpid" && ! docker exec "$CONTAINER" test -e "/proc/$spid"; then
      echo FACTORY_CONTROLLERS_STOPPED
      return 0
    fi
    sleep 0.25
  done
  docker exec "$CONTAINER" kill -TERM "$cpid" "$spid" 2>/dev/null || true
  sleep 1
  if factory_controller_pid >/dev/null 2>&1 || factory_servo_pid >/dev/null 2>&1; then
    echo FACTORY_CONTROLLERS_DID_NOT_STOP
    return 1
  fi
  echo FACTORY_CONTROLLERS_STOPPED_AFTER_TERM
}

start_custom_camera() {
  if custom_camera_launch_present && custom_camera_child_present && camera_has_frames; then
    echo CUSTOM_CAMERA_ALREADY_READY
    return 0
  fi
  sleep 2
  docker exec -d "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/third_party_ros2/third_party_ws/install/setup.bash; ros2 launch deptrum-ros-driver-aurora930 aurora930_launch.py >/tmp/nexarm-custom-camera-root.log 2>&1'
  sleep 4
  custom_camera_launch_present && custom_camera_child_present && camera_has_frames
}

start_custom_controller() {
  if custom_controller_launch_present && custom_controller_children_present; then
    echo CUSTOM_CONTROLLER_ALREADY_READY
    return 0
  fi
  docker exec -d "$CONTAINER" bash -lc 'export CHASSIS_TYPE=None; source /opt/ros/humble/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; ros2 launch sdk nexarm.launch.py >/tmp/nexarm-custom-controller-root.log 2>&1'
  for ((i=0;i<40;i++)); do
    if custom_controller_launch_present && custom_controller_children_present; then
      echo CUSTOM_CONTROLLER_READY
      return 0
    fi
    sleep 0.25
  done
  echo CUSTOM_CONTROLLER_START_FAILED
  docker exec "$CONTAINER" bash -lc 'tail -n 120 /tmp/nexarm-custom-controller-root.log 2>/dev/null || true'
  return 1
}

stop_custom_camera() {
  docker exec "$CONTAINER" bash -lc "p=\$(pgrep -u root -f '$CUSTOM_CAM' || true); [ -z \"\$p\" ] || kill -INT \$p" || true
  sleep 1
  docker exec "$CONTAINER" bash -lc 'for p in $(pgrep -u root -x aurora930_node 2>/dev/null || true); do kill -INT "$p" 2>/dev/null || true; done' || true
  for ((i=0;i<40;i++)); do
    if ! custom_camera_launch_present && ! custom_camera_child_present; then
      echo CUSTOM_CAMERA_STOPPED
      return 0
    fi
    sleep 0.25
  done
  docker exec "$CONTAINER" bash -lc 'for p in $(pgrep -u root -x aurora930_node 2>/dev/null || true); do kill -TERM "$p" 2>/dev/null || true; done' || true
  sleep 1
  ! custom_camera_child_present
}

stop_custom_controller() {
  docker exec "$CONTAINER" bash -lc "p=\$(pgrep -u root -f '$CUSTOM_CTRL' || true); [ -z \"\$p\" ] || kill -INT \$p" || true
  sleep 1
  docker exec "$CONTAINER" bash -lc 'for p in $(pgrep -u root -f "/home/ubuntu/ros2_ws/install/ros_robot_controller/lib/ros_robot_controller/ros_robot_controller" 2>/dev/null || true); do kill -INT "$p" 2>/dev/null || true; done; for p in $(pgrep -u root -f "/home/ubuntu/ros2_ws/install/servo_controller/lib/servo_controller/servo_controller" 2>/dev/null || true); do kill -INT "$p" 2>/dev/null || true; done' || true
  for ((i=0;i<40;i++)); do
    if ! custom_controller_launch_present && ! custom_controller_children_present; then
      echo CUSTOM_CONTROLLER_STOPPED
      return 0
    fi
    sleep 0.25
  done
  docker exec "$CONTAINER" bash -lc 'for p in $(pgrep -u root -f "/home/ubuntu/ros2_ws/install/ros_robot_controller/lib/ros_robot_controller/ros_robot_controller" 2>/dev/null || true); do kill -TERM "$p" 2>/dev/null || true; done; for p in $(pgrep -u root -f "/home/ubuntu/ros2_ws/install/servo_controller/lib/servo_controller/servo_controller" 2>/dev/null || true); do kill -TERM "$p" 2>/dev/null || true; done' || true
  sleep 1
  ! custom_controller_children_present
}

restore_factory_camera() {
  if factory_camera_pid >/dev/null 2>&1; then
    echo FACTORY_CAMERA_ALREADY_RUNNING
    return 0
  fi
  docker exec "$CONTAINER" test -s "$SAVED_CAM_CMD" || { echo NO_SAVED_FACTORY_CAMERA_COMMAND; return 1; }
  docker exec -d --user ubuntu "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/third_party_ros2/third_party_ws/install/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; python3 -c '\''import os; raw=open("/tmp/nexarm-factory-camera.cmdline","rb").read().split(b"\0"); a=[x.decode() for x in raw if x]; os.execv(a[0],a)'\'' >>/tmp/nexarm-factory-camera-restored.log 2>&1'
  for ((i=0;i<60;i++)); do
    factory_camera_pid >/dev/null 2>&1 && { echo FACTORY_CAMERA_RESTORED; return 0; }
    sleep 0.25
  done
  echo FACTORY_CAMERA_RESTORE_FAILED
  return 1
}

restore_factory_controllers() {
  if factory_controller_pid >/dev/null 2>&1 && factory_servo_pid >/dev/null 2>&1; then
    echo FACTORY_CONTROLLERS_ALREADY_RUNNING
    return 0
  fi
  docker exec "$CONTAINER" test -s "$SAVED_CTRL_CMD" || { echo NO_SAVED_FACTORY_CONTROLLER_COMMAND; return 1; }
  docker exec "$CONTAINER" test -s "$SAVED_SERVO_CMD" || { echo NO_SAVED_FACTORY_SERVO_COMMAND; return 1; }
  docker exec -d --user ubuntu "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; python3 -c '\''import os; raw=open("/tmp/nexarm-factory-controller.cmdline","rb").read().split(b"\0"); a=[x.decode() for x in raw if x]; os.execv(a[0],a)'\'' >>/tmp/nexarm-factory-controller-restored.log 2>&1'
  docker exec -d --user ubuntu "$CONTAINER" bash -lc 'source /opt/ros/humble/setup.bash; source /home/ubuntu/ros2_ws/install/setup.bash; python3 -c '\''import os; raw=open("/tmp/nexarm-factory-servo.cmdline","rb").read().split(b"\0"); a=[x.decode() for x in raw if x]; os.execv(a[0],a)'\'' >>/tmp/nexarm-factory-servo-restored.log 2>&1'
  for ((i=0;i<60;i++)); do
    if factory_controller_pid >/dev/null 2>&1 && factory_servo_pid >/dev/null 2>&1; then
      echo FACTORY_CONTROLLERS_RESTORED
      return 0
    fi
    sleep 0.25
  done
  echo FACTORY_CONTROLLERS_RESTORE_FAILED
  return 1
}

start_custom() {
  local camera_ok=0 ctrl_ok=0

  custom_camera_launch_present && custom_camera_child_present && camera_has_frames && camera_ok=1 || true
  custom_controller_launch_present && custom_controller_children_present && ctrl_ok=1 || true
  if ((camera_ok==1 && ctrl_ok==1)); then
    echo CUSTOM_ALREADY_READY
    return 0
  fi

  if ((ctrl_ok==0)); then
    if factory_controller_pid >/dev/null 2>&1 || factory_servo_pid >/dev/null 2>&1; then
      save_and_stop_factory_controllers || return 1
    fi
    start_custom_controller || {
      echo CUSTOM_CONTROLLER_FAILED_ROLLBACK
      stop_custom_controller || true
      restore_factory_controllers || true
      return 1
    }
  fi

  if ((camera_ok==0)); then
    if factory_camera_pid >/dev/null 2>&1; then
      save_and_stop_factory_camera || {
        stop_custom_controller || true
        restore_factory_controllers || true
        return 1
      }
    fi
    start_custom_camera || {
      echo CUSTOM_CAMERA_FAILED_ROLLBACK
      stop_custom_camera || true
      stop_custom_controller || true
      restore_factory_controllers || true
      restore_factory_camera || true
      return 1
    }
  fi

  if custom_controller_children_present && custom_camera_child_present && camera_has_frames; then
    echo CUSTOM_READY_CAMERA_AND_CONTROLLER_ROOT
    return 0
  fi

  echo CUSTOM_VERIFY_FAILED_ROLLBACK
  stop_custom_camera || true
  stop_custom_controller || true
  restore_factory_controllers || true
  restore_factory_camera || true
  return 1
}

return_factory() {
  stop_custom_controller || true
  stop_custom_camera || true
  sleep 1
  restore_factory_controllers
  restore_factory_camera
  echo FACTORY_READY
}

status() {
  local fcam=0 ccam=0 fctrl=0 cctrl=0 bringup=0
  factory_camera_pid >/dev/null 2>&1 && fcam=1 || true
  { custom_camera_launch_present || custom_camera_child_present; } && ccam=1 || true
  { factory_controller_pid >/dev/null 2>&1 && factory_servo_pid >/dev/null 2>&1; } && fctrl=1 || true
  { custom_controller_launch_present && custom_controller_children_present; } && cctrl=1 || true
  docker exec "$CONTAINER" pgrep -u ubuntu -f '^/usr/bin/python3 /opt/ros/humble/bin/ros2 launch bringup bringup.launch.py$' >/dev/null 2>&1 && bringup=1 || true
  echo "factory_camera=$fcam custom_camera=$ccam factory_controller=$fctrl custom_controller=$cctrl bringup=$bringup"
  if ((fcam==1 && ccam==0 && fctrl==1 && cctrl==0)); then echo MODE=FACTORY; return 0; fi
  if ((fcam==0 && ccam==1 && fctrl==0 && cctrl==1)); then echo MODE=CUSTOM; return 0; fi
  echo MODE=INCOMPLETE
  return 0
}

case "${1:-status}" in
  start|custom) start_custom ;;
  stop|factory) return_factory ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|status}"; exit 2 ;;
esac
