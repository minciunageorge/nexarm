# nexarm

Development and deployment scaffold for a **64-bit Raspberry Pi 5**, SSH user
`pi`, and Docker container `nexarm` running **ROS2 Humble / Ubuntu 22.04**.
The repository initially contains no robot packages or hardware configuration.

For laptop-independent unattended operation, the preferred simpler architecture is
[the Pi-hosted ARM64 runner with a read-only broker](docs/pi-self-hosted-runner.md).
The [VPS relay architecture](docs/unattended-operation.md) remains an inactive alternative.

For the real Pi with an already-running container, follow
[the preservation bootstrap procedure](docs/raspberry-pi-bootstrap.md). Use
`collect-existing` for read-only collection; the deployment examples below apply
only after a separately authorized migration, never during this bootstrap.

## Safety boundary

No deployment happens on push. The CLI defaults to a dry run for deployment,
rollback, and camera capture; use `--apply` deliberately. Probe and collection
are read-only operations apart from local artifact output. An existing container
named `nexarm` is never replaced unless it has `org.nexarm.managed=true` and
`--replace-managed` is passed. **Do not relabel an existing robot container to
bypass this check.** Inventory and migrate that deployment separately.

Containers have a read-only root, a temporary `/tmp`, no Linux capabilities,
no privilege escalation, no device mounts, a non-root `pi` UID/GID, bounded
resources, and no automatic restart. Results persist outside the container.
Networking defaults to `none`. Host networking is an explicit option and uses
ROS domain 42; enabling it exposes the container to ROS traffic on that domain.
Domain separation is not authentication or a robot emergency stop.
This scaffold starts only `sleep infinity`; it never launches robot drivers,
actuators, cameras, or modifies firmware, GPIO, udev, host networking or Docker
configuration. Physical safety procedures remain necessary for later robot code.

## Prerequisites and secure connection

Use the existing isolated checkout; do not create a worktree for routine tasks.
The operator needs Python 3.10+, Git, OpenSSH, and an authenticated SSH route.
The Pi needs a 64-bit ARM Linux OS, Docker Engine, `bash`, `tar`, `flock`, and
working Docker permissions for `pi`. Docker access is effectively host-root
access; bootstrap reports readiness and does not install Docker or change groups.
The Pi must reach Docker Hub and Ubuntu/ROS apt repositories for image builds.

The known local target is `pi@192.168.100.153`. Cloud access to this private
address is **not assumed**. Run locally or establish a user-managed authenticated
VPN/SSH tunnel later. Do not expose Docker's API or unauthenticated SSH endpoints.
Verify the Pi host-key fingerprint out of band and populate `known_hosts` using
your normal trusted SSH provisioning. The CLI requires strict host-key checking
and noninteractive authentication, never embeds keys, and never accepts passwords.
A tunnel endpoint such as `127.0.0.1:2222` must have the verified Pi host key for
`[127.0.0.1]:2222` in the operator's SSH configuration.

```sh
python3 tools/nexarm.py probe --host 192.168.100.153
# Example for an already-established secure tunnel:
python3 tools/nexarm.py probe --host 127.0.0.1 --port 2222 --identity /secure/path/pi_key
```

`probe` validates ARM64 and Docker access. It does not configure robot hardware.

## Develop, validate, deploy

Add ROS2 packages under `ros_ws/src`. The pinned ROS base supplies ROS dependencies
and colcon; the Dockerfile builds packages and runs their tests during the image build; future
package-specific dependencies must be declared and explicitly integrated into
the image. No packages currently exist, so colcon's empty test result must not
be treated as application test coverage. The ROS node lifecycle smoke check
validates the runtime; future application smoke checks must be added separately.

```sh
python3 -m unittest discover -s tests -v
for script in deploy/*.sh; do bash -n "$script"; done
docker build -t nexarm:dev .
# On an ARM64 builder, add --platform linux/arm64.
docker run --rm -i --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user 1000:1000 \
  --tmpfs /tmp --tmpfs /results:uid=1000,gid=1000 nexarm:dev \
  python3 < tests/ros_runtime.py

git add . && git commit -m "Describe tested change"
python3 tools/nexarm.py deploy --host 192.168.100.153           # dry run
python3 tools/nexarm.py deploy --host 192.168.100.153 --apply   # first install
python3 tools/nexarm.py deploy --host 192.168.100.153 --apply --replace-managed
```

Deployment requires a clean committed checkout. It sends a Git archive over SSH,
builds `nexarm:<commit>` on the Pi for `linux/arm64`, and only stops the old
managed container after a successful build. A lock prevents concurrent changes.
The old container is retained during smoke validation and restored automatically
if startup or smoke validation fails. Successful updates save the previous image
ID for explicit rollback. Abrupt host/process loss can leave `nexarm-rollback`;
inspect both containers and their logs before manually recovering. Automatic
rollback handles ordinary command failures, not host power loss.

```sh
python3 tools/nexarm.py rollback --host 192.168.100.153 --apply --replace-managed
python3 tools/nexarm.py collect --host 192.168.100.153
```

Rollback uses the previous successful image with the requested networking mode
(default `none`); failed-update automatic rollback preserves the exact old
container settings. Persistent results are not rolled back. No images are pruned.
Build contexts are cleaned. The base image is pinned to an upstream multi-platform digest; review updates
deliberately before changing it.

GitHub CI runs framework tests, shell validation, an ARM64 image build, and a ROS
runtime smoke check under emulation. It does not connect to the Pi or deploy.
For unattended deployment later, run this CLI from a trusted runner with the
verified SSH route and separately approved deployment credentials; retain the
explicit deployment gate and capture artifacts even on failure.

## Logs, test results, and camera images

Deploy, rollback, and camera operations automatically attempt artifact collection,
including after failure. A failed collection is reported; local operation logs
remain in ignored `artifacts/`. `collect` retrieves a timestamped `.tar.gz` with
Docker stdout/stderr, limited status metadata, ROS logs, colcon build/test logs,
smoke results, XML test results, and camera outputs. Collection copies files without stopping the
container; active files may be changing. Protect these archives as potentially
sensitive operational data. No automatic upload or retention deletion is enabled.
Pi storage is `~pi/.local/share/nexarm/results`; monitor capacity and retain or
remove artifacts deliberately. Docker stdout/stderr uses the host's existing
logging policy; this scaffold does not change it.

Camera capture subscribes to one explicitly selected `sensor_msgs/msg/Image`
topic, writes `latest.ppm` and JSON timestamp/frame metadata, and fails after
20 seconds with no frame. It supports `rgb8`, `bgr8`, and `mono8`, including row
padding; other encodings fail explicitly. PPM is viewable or convertible with
standard image tools. No camera driver is started or configured.

To observe an existing camera publisher on domain 42 through the host, explicitly
redeploy with networking enabled, then request capture:

```sh
python3 tools/nexarm.py deploy --host 192.168.100.153 --apply --replace-managed --ros-network host
python3 tools/nexarm.py camera --host 192.168.100.153 --topic /camera/image_raw --apply
```

The topic and domain are examples, not inferred hardware settings. Review the
actual ROS topology before enabling host networking. Do not change the robot's
current domain or camera configuration to match these examples. Device access,
application launch, domain customization, and robot integration are future,
separately reviewed changes.
