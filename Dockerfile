FROM ros:humble-ros-base-jammy@sha256:860e53793ccf575e5369b5e9a19939de2bfcfb46a299954d06f54f2af54fed19
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
COPY ros_ws /opt/nexarm/ws
RUN source /opt/ros/humble/setup.bash && cd /opt/nexarm/ws && colcon build && ROS_LOCALHOST_ONLY=1 colcon test && colcon test-result --verbose && mkdir -p /opt/nexarm/test-results && if [ -d build ]; then find build -type f -name "*.xml" -exec cp --parents -t /opt/nexarm/test-results/ {} + ; fi
COPY tools/camera_capture.py /opt/nexarm/camera_capture.py
COPY deploy/entrypoint.sh /opt/nexarm/entrypoint.sh
RUN chmod 755 /opt/nexarm/entrypoint.sh && chmod 644 /opt/nexarm/camera_capture.py
ENV ROS_LOG_DIR=/results/ros ROS_DOMAIN_ID=42 ROS_LOCALHOST_ONLY=1 HOME=/tmp
WORKDIR /opt/nexarm/ws
ENTRYPOINT ["/opt/nexarm/entrypoint.sh"]
CMD ["sleep", "infinity"]
