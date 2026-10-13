# Issue #1: approved ROS2 and Aurora still-frame diagnostics

This stage adds only two fixed broker commands:

| Command | Behavior |
| --- | --- |
| `ros-inspect` | Discover ROS nodes/topics for three seconds, return JSON with the inherited domain and middleware identifier |
| `camera-one-frame` | Subscribe to `/depth_cam/rgb/image_raw`, export exactly one 640×400 RGB still and timestamp metadata |

The existing `nexarm` container ID, start time, configuration and runner registration
are preserved. No driver, actuator publisher, robot/servo motion, application
launch, deployment or arbitrary shell/exec interface is enabled. The previous
container-preservation guard remains unchanged.

## Public repository and workflow control

The owner explicitly permits this repository to remain public for this stage.
No visibility change, separate automation repository or metadata-read token is
required. Pi workflows are manual `workflow_dispatch` jobs restricted to main;
PR/fork CI runs on GitHub-hosted runners. Keep workflow writers trusted, protect
main, and restrict the `pi-diagnostics` and `pi-readonly` environments to main.
A public self-hosted runner is not a sandbox for untrusted code: repository writers
can introduce other workflows, and the fixed broker limits only privileged operations.

## Minimum one-time activation

Run as **pi**, while the runner is idle, from your existing SSH session:

```sh
curl -fL https://raw.githubusercontent.com/minciunageorge/nexarm/main/bootstrap/activate-nexarm-runner.py -o /home/pi/activate-nexarm-diagnostics.py && python3 /home/pi/activate-nexarm-diagnostics.py
```

Use a new filename if the destination contains your own changes. Accept the default
repository. An already registered runner needs no registration or metadata token.
The standalone script checks the preserved registration URL and container ID/guard,
then upgrades only recognized original, previously visibility-gated or current
broker files and the exact sudoers policy. Known historical backups are preserved;
unknown/custom files are never reset. Partial application is resumable. The config
retains its container ID and artifact path allowlist. Registration, account, guard,
container and current compatible service unit are not replaced or recreated.
New ROS/camera operations still require root-owned `diagnostics_enabled: true`.
Existing probe/discovery/collection do not require this new enrollment.

Any metadata token/helper left by the previous version is unused and is not read,
overwritten or exported. The upgrade does not require GitHub API connectivity.

## Remote operation

Select **Pi approved ROS and Aurora diagnostics** → Run workflow → main, with
`ros-inspect` or explicitly `camera-one-frame`. An authenticated Codex Actions
integration can dispatch/poll/download the same workflow; the current cloud's
GitHub API requests return Forbidden, so live dispatch is not available here.
No SSH copy/paste is needed for individual diagnostics once enrollment is complete.

Artifacts contain either `metadata.txt` for the ROS graph or `results.tar.gz` with
`aurora-rgb.ppm` and `camera.json` for one still frame. PPM is a standard lossless
RGB image format; open/convert it with common image tools. Failure diagnostics go
in `broker-error.log`. Artifacts follow GitHub repository access rules with seven-day retention and are
removed from the runner job workspace afterwards.

## Execution and limits

Only root-installed trusted observer code is sent over stdin to a fixed Docker
exec command, using UID/GID **65534:65534**, `/usr/bin/python3 -I`, fixed Humble
Python paths, and a fixed operation. No shell, user script, user topic, namespace,
container name/ID, device mount or command override is accepted. No payload is
copied into the container, and no image file is written there. Transient ROS
middleware state/logging may use existing `/tmp`; subscribing/discovery are the
only intended ROS interactions. They do not publish actuator messages or invoke
robot services. The observer uses the container's configured ROS_DOMAIN_ID/default,
not a hardware/domain reconfiguration.

The camera waits at most 15 seconds, accepts only 640×400 rgb8/bgr8 with bounded
stride, and converts one frame to RGB. No compressed/depth topic or continuous
stream capture is offered. The observer has a 22-second OS alarm, 15 CPU-second
limit and 512 MiB virtual-address limit; the Docker client has a 25-second timeout
and 4 MiB output/error-file limits. The OS alarm terminates the actual container
observer even if the host Docker client is killed; no Docker/container kill is used.
Graph results are limited to 200 nodes/topics. Before/after diagnostic state is
checked; outputs from any failing operation are discarded by the job adapter.

The runner still has no Docker-group membership, broad sudo, broker-upgrade command
or deployment authority. Its only new sudoers entries are the two exact operations
above. Root-installed helpers never import workflow checkout code. The root-owned
upgrade script is operator-run only and is not in the runner's sudoers policy.

Existing container dependencies must already include ROS2 Humble Python 3.10,
rclpy and sensor_msgs. Missing libraries, DDS permissions, an entrypoint-only domain
setting, unsupported encoding/resolution or absent publisher fail clearly rather
than triggering package installation, camera launch, root-in-container fallback or
hardware changes. Non-root middleware/shared-memory compatibility on the actual
Pi is not yet verified; review actual graph/domain results before expecting images.

## Validation

Local tests cover enrollment failure before Docker access, fixed non-root exec argv,
strict output validation, old/new broker upgrade recognition and workflow gates.
A disposable native x86-64 Humble container demonstrates ROS graph inspection,
synthetic 640×400 image capture as UID 65534, and missing-publisher timeout. Hosted
CI repeats that integration test; ordinary tests/builds never select the Pi runner.
These are distinct from ARM64/live Aurora tests, which remain unrun. No command in
this implementation session connected to, upgraded or operated the real Pi.
