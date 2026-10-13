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

## Public repository: blocked until private

**Do not run a robot-hosted runner in an untrusted public repository.** All shipped
Pi workflows now require `github.event.repository.private == true` on main at the
job level, before allocating the runner. The new broker also independently checks
GitHub's authenticated repository metadata on **every operation**, including old
probe/discovery/collection. Public visibility, unavailable API, revoked/expired
credentials or unenrolled diagnostics fail closed before any Docker operation.
A workflow-supplied `private=true` value cannot bypass this root-owned check.

Labels, protected branches and these workflow checks do not make arbitrary public
workflow execution safe. Another workflow could target a public registered runner,
and untrusted host code could access networks without using this broker. The
minimum activation requirement is to change **this existing repository to private**
and keep workflow authors trusted. That visibility change preserves its URL and
runner registration. If that is impossible, do not activate this stage; use a
separate private automation repository and a separately authorized runner migration.
This stage refuses a selected repository that differs from the existing registration.
If the public runner previously ran untrusted code, restore trust in that host/workspace
before allowing any new privilege; a visibility flip does not undo a compromise.

No public-repository diagnostic run is dispatched by this implementation. Existing
public workflows will skip, not report a passed physical-camera test.

## Minimum one-time activation

1. **While the repository is still public**, download the reviewed standalone script
   into a new file from your existing pi SSH session (download only; no execution):

   ```sh
   curl -fL https://raw.githubusercontent.com/minciunageorge/nexarm/main/bootstrap/activate-nexarm-runner.py \
     -o /home/pi/activate-nexarm-diagnostics.py
   ```

   Use a new filename if that file already contains your own changes. If the
   repository is already private, download through your authenticated GitHub UI
   and transfer that one file using your existing trusted connection. Unauthenticated
   raw download will not work after making the repository private.

2. Change repository visibility to private in GitHub settings. Protect main and
   create `pi-diagnostics`, restricted to main. A required reviewer can provide an
   additional per-run gate if desired and supported by the GitHub plan. Keep all
   workflows and repository write access trusted; no PR workflow targets the Pi.

3. Create a fine-grained GitHub token scoped only to this repository with **Metadata:
   read** permission (no Contents write, Actions write or administration needed).
   This token is for the Pi's runtime privacy check, distinct from a runner
   registration token or ChatGPT's Actions API access. Enter it only at the script's
   hidden local prompt, never in chat. Then run **as pi, while the runner is idle**:

   ```sh
   python3 /home/pi/activate-nexarm-diagnostics.py
   ```

The standalone script contains all upgrade files. It authenticates repository
privacy before changing host permissions, checks the registered repository URL,
verifies the existing container ID/guard, and upgrades only recognized old/current
broker files and exact sudoers policy. Custom/unsafe files fail for review; known
old files are backed up. Partial application is resumable: matching desired files
are accepted and missing new helper modules are installed. Unknown files are
never reset to force success. The root-owned config retains its container ID and
existing artifact path allowlist. Registration, account, guard, container and
current compatible service unit are not replaced or recreated.

The token is stored only in `/etc/nexarm-runner/github-metadata.token`, root-owned
mode 600 inside a root-only config directory, and never passed into the container,
workflow environment, command-line arguments or artifacts. Its value is sent to
GitHub's HTTPS metadata endpoint only. TLS verification remains enabled. Expiry,
rate limiting or network failure denies operations; renew the credential by
rerunning the same reviewed bootstrap with a valid token. The runner service's
startup probe also fails closed when repository privacy cannot be verified. The
robot application is not stopped or altered by an API outage.

## Remote operation

Select **Pi approved ROS and Aurora diagnostics** → Run workflow → main, with
`ros-inspect` or explicitly `camera-one-frame`. An authenticated Codex Actions
integration can dispatch/poll/download the same workflow; the current cloud's
GitHub API requests return Forbidden, so live dispatch is not available here.
No SSH copy/paste is needed for individual diagnostics once enrollment is complete.

Artifacts contain either `metadata.txt` for the ROS graph or `results.tar.gz` with
`aurora-rgb.ppm` and `camera.json` for one still frame. PPM is a standard lossless
RGB image format; open/convert it with common image tools. Failure diagnostics go
in `broker-error.log`. Artifacts are private with seven-day retention and are
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

Local tests cover privacy failure before Docker access, fixed non-root exec argv,
strict output validation, old/new broker upgrade recognition and workflow gates.
A disposable native x86-64 Humble container demonstrates ROS graph inspection,
synthetic 640×400 image capture as UID 65534, and missing-publisher timeout. Hosted
CI repeats that integration test; ordinary tests/builds never select the Pi runner.
These are distinct from ARM64/live Aurora tests, which remain unrun. No command in
this implementation session connected to, upgraded or operated the real Pi.
