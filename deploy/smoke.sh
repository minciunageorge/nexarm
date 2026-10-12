#!/usr/bin/env bash
set -euo pipefail
# Exercises ROS middleware locally; no robot commands or hardware nodes.
docker exec nexarm /opt/nexarm/entrypoint.sh python3 -c 'import rclpy; rclpy.init(); n=rclpy.create_node("nexarm_smoke"); assert n.get_name() == "nexarm_smoke"; n.destroy_node(); rclpy.shutdown()'
