# Issue #1: approved ROS2 and Aurora still-frame diagnostics

This stage provides these fixed broker commands:

| Command | Behavior |
| --- | --- |
| `ros-inspect` | Discover ROS nodes/topics for three seconds, return JSON with the inherited domain/middleware and exact fixed RGB publisher count/offered QoS |
| `aurora-health` | Observe fixed RGB/depth/IR message counters and header timestamps for ten seconds; no images saved |
| `camera-one-frame` | Subscribe to `/depth_cam/rgb/image_raw`, export exactly one 640×400 RGB still and timestamp metadata |

The existing `nexarm` container ID, start time, configuration and runner registration
are preserved. No driver, actuator publisher, robot/servo motion, application
launch, deployment or arbitrary shell/exec interface is enabled. The previous
container-preservation guard remains unchanged.

## Public repository and workflow control

The owner explicitly permits this repository to remain public for this stage.
No visibility change, separate automation repository or metadata-read token is
required. Pi workflows are restricted to main. Diagnostics also retain the existing
owner-added push triggers for `.nexarm/camera-trigger.txt` and
`.nexarm/ros-trigger.txt` plus the new `.nexarm/aurora-trigger.txt`; ordinary source pushes do not target the Pi.
PR/fork CI runs on GitHub-hosted runners. Keep workflow writers trusted, protect
main, and restrict the `pi-diagnostics` and `pi-readonly` environments to main.
A public self-hosted runner is not a sandbox for untrusted code: repository writers
can introduce other workflows, and the fixed broker limits only privileged operations.

## Minimum one-time activation

Run as **pi**, while the runner is idle, from your existing SSH session:

```sh
curl -fL https://raw.githubusercontent.com/minciunageorge/nexarm/main/bootstrap/activate-nexarm-runner.py -o /home/pi/activate-nexarm-diagnostics.py && python3 /home/pi/activate-nexarm-diagnostics.py
```

If activation stopped with `Unexpected UTF-8 BOM`, redownload this standalone
script and rerun it; no runner removal, re-registration or hand-editing `.runner`
is needed. UTF-8 registration/config JSON with or without a BOM is accepted.

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

`ros-inspect` now includes `camera`: the fixed topic, publisher count and each
publisher's node/type and offered reliability, durability, history, depth,
deadline, lifespan and liveliness. Counts/endpoints are a discovery snapshot and
may change as publishers join/leave. The primary reader QoS is explicit
**RELIABLE / VOLATILE / KEEP_LAST depth 5**, matching the reported live Aurora
writer. It is tried for up to eight seconds. If no frame arrives, the observer
destroys that subscription and tries **BEST_EFFORT / VOLATILE** for the remainder
of the same 15-second total deadline. The fallback preserves support for
best-effort writers. Only one frame is retained/exported; no camera or publisher
is launched. Discovery reports include both requested profiles; successful
`camera.json` records `selected_qos` and delivery attempts.

On timeout, `broker-error.log` contains bounded fixed-topic JSON with discovered
publisher count, offered reliability/durability, and per-attempt current/max
matched-publisher and received-message counts. A discovered publisher with zero
matches indicates no observed DDS match; a match with zero messages distinguishes
matching from actual callback delivery. A match does not prove that the camera
is producing frames or that transport delivers samples. These are local RMW
snapshots, not proof of why the live camera timed out. Raw container stderr is
not forwarded. The job fails and exports no image archive on timeout.

Rerun the newly downloaded standalone activation script as pi while the runner is
idle to install this root-owned observer update. The upgrade recognizes the
previous observer's exact bytes, preserves its backup and is safely repeatable;
custom helper files still fail closed. No runner re-registration is required.

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
synthetic 640×400 image capture as UID 65534 across BEST_EFFORT/RELIABLE and
VOLATILE/TRANSIENT_LOCAL writers, publisher-count/offered-QoS reporting, and
missing-publisher timeout. Hosted
CI repeats that integration test; ordinary tests/builds never select the Pi runner.
These are distinct from ARM64/live Aurora tests, which remain unrun. No command in
this implementation session connected to, upgraded or operated the real Pi.

## Aurora stream health

After rerunning the newly downloaded bootstrap as pi while the runner is idle,
select `aurora-health` in **Pi approved ROS and Aurora diagnostics** on main.
For the existing commit-trigger route, change `.nexarm/aurora-trigger.txt` on main
with `Aurora health` in the commit message only when a diagnostic run is requested.
This implementation does not create/change any trigger file or dispatch a Pi job.
The root-owned broker adds only that exact sudoers command; registration, account,
preservation guard and container remain unchanged. No package installation,
deployment, camera launch, USB/device access or robot motion is enabled.

The fixed topics are `/depth_cam/rgb/image_raw`, `/depth_cam/depth/image_raw` and
`/depth_cam/ir/image_raw`, all `sensor_msgs/msg/Image`. Other names are not subscribed
to; a missing/unobserved topic is not evidence of a stalled driver. Use `ros-inspect`
to identify a different topic name before requesting an explicit allowlist update.

The ten-second observer creates RELIABLE/VOLATILE and BEST_EFFORT/VOLATILE readers
with depth 1 on each topic. It exports only JSON in `metadata.txt`, discarding image
pixels. Each reader reports its local callback count, first/last header timestamp,
timestamp advances/repeats/regressions, first/last local arrival time and current/max
DDS matched-publisher count. Counts are per reader; never sum them as unique frames.
These are observer counters, not driver-owned counters or USB statistics.

`rgb_silent_other_streams_delivering` indicates RGB silence while depth or IR arrives.
`all_observed_streams_silent` indicates no callbacks across the three observed topics;
it does not identify USB, driver, node or DDS transport as the cause. Per-stream
statuses distinguish discovered/unmatched, matched/silent and missing publishers.
Advancing counters with frozen timestamps are also distinguished from no delivery.
No messages is a successful diagnostic result rather than a failed frame capture.
Existing 22-second observer alarm, CPU/memory and 25-second Docker-client limits apply.

Tests cover RGB-only silence, all-stream silence with matching publishers, advancing
and frozen stamps, missing/unmatched publishers and safe upgrade recognition. Native
Humble integration uses only disposable synthetic publishers; physical Pi/Aurora
behavior is untested by the cloud environment.

## Aurora process/component/USB discovery and recovery status

The current authorized next step is **discovery first**. The known official launch
signature is `ros2 launch deptrum-ros-driver-aurora930 aurora930_launch.py` inside
nexarm; known node names are `/aurora` and `/depth_cam`. A launch signature or node
name alone does not prove that a process restart is camera-only.

Rerun the newly downloaded bootstrap as pi while idle, then select
`aurora-discover` in the existing diagnostics workflow. The commit-trigger route
also accepts changes to `.nexarm/aurora-discovery-trigger.txt` with `Aurora discover`
in the commit message. No trigger file is changed by this implementation.

The `metadata.txt` result contains:

- Camera node services and the owners of the three fixed Image publishers.
- Read-only ROS component-manager `ListNodes` responses, including other loaded
  nodes, or explicit unavailable/timeout results within a four-second total budget.
- Matching DePtrum launch/process candidates and descendants: PID/parent, UID,
  executable if readable, state, thread count and wait channel. Component-container
  candidates are flagged. Full command lines, environment, descriptors and process
  memory are never exported. Candidate PIDs are not proven ROS-node/PID mappings.
- Host and container USB sysfs presence, vendor/product IDs, authorization and
  interface bindings; container USB-node ownership/modes/access as UID 65534.
  No USB device is opened, reset, unbound or modified; serial numbers and broad
  kernel logs are not collected. Presence/binding does not prove successful transfers.
- Fast DDS shared-memory ownership/access metadata. A root-owned writer and the
  unprivileged observer may have a transport/permission issue even when DDS matches;
  all-stream silence does not by itself prove driver/USB failure.

`aurora-recover` currently **refuses all restart actions**, returns the same discovery
report and automatically rechecks ten-second stream health. It is report-only:
there is no process signal, shell command, camera launch, lifecycle state change,
component load/unload, USB reset or container restart implementation. Even detecting
lifecycle services cannot enable it. Bootstrap preserves this refusal and cannot
arm recovery through configuration. Runner registration/container/account/guard are
preserved; only exact discovery/report commands are added to sudoers.

The live process/component topology and restart mechanism cannot be proven from
this cloud workspace. Review the discovery artifact first. A subsequent narrowly
approved implementation can then add the verified camera-only restart, followed by
stream-health and one-frame checks automatically. **That mutation stage is not yet
implemented or enabled**, following the user's clarification to remain read-only
unless isolation is proven. Do not manually kill a PID or rerun the launch command
on the basis of this report; it may create a duplicate driver or affect shared code.

Tests use synthetic ROS/process/USB fixtures and disposable local containers; no
live Pi process inspection or camera restart was performed by this change.

## Discovery failure diagnostics

For `aurora-discover` / report-only `aurora-recover`, failed observer invocations
now retain the real exit code and stderr in `broker-error.log` as JSON, rather than
only `Fixed ROS observer failed`. Stderr is capped at 64 KiB, preserving its first
16 KiB and last 48 KiB if truncated; the original byte count and truncation flag
are included. Docker-client timeout and possible signal exit are identified.
The existing 22-second SIGALRM still terminates the observer; faulthandler prints
its current Python stack before chaining to that termination.

Optional process, component, USB/device and shared-memory probes report `partial`
with per-section error type/message/traceback when permissions, hotplug races,
probe limits or optional API failures prevent inspection. Independent node and
publisher evidence is retained. Host USB probe failures are also recorded rather
than dropping the container discovery report. A partial/unavailable probe must
not be interpreted as evidence of healthy, absent or isolated hardware.

The reported live artifact lost the underlying stderr in the previous version,
so its exact root cause cannot be established retrospectively here. Tests
reproduce the permission-denied failure path under UID 65534 in a disposable
container and confirm discovery returns useful partial evidence. Rerun the new
bootstrap while idle, then `aurora-discover`; an unexpected fatal error now exports
its actual cause. Restart remains disabled regardless of discovery success.

## Large ROS graphs

Discovery filters the graph for the two known Aurora node names and read-only
component-manager services before applying output limits. It no longer fails
merely because the graph exceeds 200 nodes/services. The report includes total
and scanned node/service counts and explicit truncation flags. Each graph scan
is capped at 4096 entries; only the two unique known camera nodes are emitted.
A truncated scan is `partial` and cannot prove that an omitted node is absent.
Process and USB probes still run independently.

At most 16 unique component-manager services are queried, prioritizing known
camera paths and DePtrum names, within the existing four-second deadline. Large
component membership responses retain at most 200 aligned node/ID pairs, prioritize
known camera nodes within a 4096-entry scan, and report total/scanned counts and
truncation. No restart is enabled and existing output/CPU/memory/time limits remain.

The exact live failure `RuntimeError: Discovery graph limit exceeded` is addressed
by this filtering/partial-result policy. Rerun the updated bootstrap while idle,
then `aurora-discover`. Regression tests include a 5000-entry graph and a native
Humble graph with more than 200 services; this does not constitute a live Pi test.
