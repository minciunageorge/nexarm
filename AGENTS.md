# Development rules

Use this existing isolated checkout; do not create a worktree unless requested.
The target is a Raspberry Pi 5, user pi, container nexarm, ROS2 Humble, ARM64.
The private address 192.168.100.153 is a local hint, not guaranteed cloud access.

Keep robot hardware, firmware, drivers, GPIO, device mounts and actuator launch
configuration unchanged unless the user explicitly authorizes that integration.
Do not deploy automatically as part of ordinary code changes. Prefer local
container validation and deployment dry runs. Never relabel an unmanaged nexarm
container to make replacement possible. Use verified SSH host keys and a secure
user-managed route; do not store credentials or artifacts in Git.

Run `python3 -m unittest discover -s tests -v` and syntax-check each shell script.
Build the Docker image and run `tests/ros_runtime.py` inside the restricted
container as documented in README.md for runtime changes. These checks use
synthetic ROS images and do not validate physical robot/camera behavior.
Report ARM64, Pi connectivity and hardware checks as untested unless executed.

The preferred transport is now a dedicated ARM64 self-hosted GitHub runner on the
Pi with NO Docker/sudo-group membership, plus a root-owned read-only broker; see
docs/pi-self-hosted-runner.md. The VPS relay is an inactive alternative. The user has activated the Pi runner and reported a live verification failure;
cloud code must not modify the Pi. The service upgrade is prepared for the user
to apply as pi while idle. Existing nexarm must stay
unchanged. Routine jobs use only the root-installed approved operations. Issue #1 adds
fixed ROS graph inspection and one Aurora RGB still; arbitrary exec, driver
launch, deployment and motion remain disabled. Do not register a
robot-hosted runner to a public/untrusted repository. Verify Actions API binding
before claiming ChatGPT can dispatch/download results; Git proxy auth is not API
access. Do not run untrusted tests on the robot or relax the broker during setup.

Issue #1 explicitly authorizes the two fixed root-installed diagnostics
ros-inspect and camera-one-frame (Aurora /depth_cam/rgb/image_raw, 640x400).
It does not authorize arbitrary exec, motion, deployment or hardware changes.
All Pi workflows must fail closed on public repositories. Before new Pi broker
permissions, authenticate that the preserved registered repository is private;
see docs/ros-aurora-diagnostics.md. Never dispatch against the currently public
repository or enroll runtime credentials from chat. Current diagnostics remain
prepared/unactivated on the live Pi until the user performs that private upgrade.
