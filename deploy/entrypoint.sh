#!/usr/bin/env bash
set -euo pipefail
set +u
source /opt/ros/humble/setup.bash
if [[ -f /opt/nexarm/ws/install/setup.bash ]]; then source /opt/nexarm/ws/install/setup.bash; fi
set -u
mkdir -p "$ROS_LOG_DIR"
exec "$@"
