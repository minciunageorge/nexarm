# Minimal activation from your current pi SSH session

No VPS/tunnel is needed. The downloadable **standalone activation script embeds
all broker, sudoers, protection and service files**, so no repository clone or
multi-command host installer is required on the Pi.

## Before running

The owner permits the public `minciunageorge/nexarm` repository. Use trusted
workflows, protected main and main-only `pi-readonly` / `pi-diagnostics` environments.
No visibility change or metadata token is required. See
[the diagnostics upgrade procedure](ros-aurora-diagnostics.md) for an existing runner.
Never run untrusted PR/fork code on the Pi.

## The Pi command

Transfer the supplied `activate-nexarm-runner.py` file once into `/home/pi/` using
your existing trusted file-transfer method. In your already-open SSH session as
**pi**, run:

```sh
python3 /home/pi/activate-nexarm-runner.py
```

Do **not** run the Python script with sudo. It asks sudo only for the reviewed host
setup, registration as the dedicated user, and installation/start of its own
service. It asks for:

1. The repository (`minciunageorge/nexarm` is suggested).
2. GitHub's short-lived **runner registration token** from Settings → Actions →
   Runners → New self-hosted runner → Linux ARM64. Paste only at the hidden prompt
   in your SSH session; never paste the token into ChatGPT.

The script fetches GitHub's official latest stable Linux ARM64 runner metadata,
downloads its release asset, checks its published SHA256 digest, checks archive
paths, and extracts as `nexarm-runner`. It fails if a digest is unavailable or
mismatched; no verification is bypassed. It does not install OS dependencies,
change the OS/Docker configuration or overwrite partial/changed installations.
Missing runner libraries must be diagnosed before considering a separate host
package change. Initial package verification is distinct from future runner updates.

The registration token is passed over local stdin into GitHub runner's supported
`ACTIONS_RUNNER_INPUT_TOKEN` environment input, not command arguments or shell
history. Registration credentials remain within the dedicated mode-700 account
home; none are included in upload artifacts. Token input fails closed if empty.

The script prepares the existing protection guard, registers the runner, enables
`nexarm-actions.service` at boot and starts that service only. It checks the
existing container's ID, running state and start timestamp before/after. It
contains no container exec/stop/recreate, deployment, camera startup or motion
command. Future application jobs remain disabled. Reviewed exact matching host
files allow retry of an interrupted setup; existing mismatching files or an
unconfigured partial package directory stop for operator inspection. A matching
already-active service is not restarted or re-registered.

Runner registration and the service being active do not prove GitHub jobs work.
The service/host files may have been prepared even if a later network, library or
registration step fails. The script never undoes unrelated files or changes the
robot to repair such a failure.

## Harmless verification after activation

In the repository's Actions tab, select **Pi harmless verification**,
then **Run workflow** on `main`. Once GitHub Actions API access is securely
configured for Codex, Codex can dispatch and retrieve this same job instead of
you using the UI. That API integration is not established by runner registration
or the platform's existing Git clone credentials.

The workflow:

- Reports hostname, OS/kernel, ARM64 architecture, CPU count and runner UID/groups.
- Verifies the runner cannot directly connect to the Docker socket.
- Uses only the installed broker for approved container status, ROS-path metadata
  discovery, and bounded existing Docker logs/result collection.
- Compares before/after container diagnostic state, including start timestamp.
- Uploads a seven-day verification artifact and removes job result files.

It does not start ROS nodes, read camera devices, run tests in the robot container,
deploy or execute any motion. Initial `artifact_paths=[]` means ROS log/camera file
access is **not yet tested**; the verification report explicitly says so. Only
known narrow directories reviewed into the root-owned allowlist can later be
collected. Docker logs and path metadata are usable immediately.

Download the resulting artifact to inspect `system.json`, `verification.json`,
`diagnostic-paths.txt`, before/after container status and `diagnostics.tar.gz`.
A passed job and expected files establish read-only readiness; do not use an
online runner badge alone. If the job fails, retain its diagnostics and report
the error without relaxing Docker permissions or modifying nexarm.

No Pi activation, GitHub workflow dispatch, or live ROS/camera validation has
been performed from the Codex environment. Public release API access is blocked
by this cloud instance's egress proxy; the script's live release download remains
to be tested from the Pi's normal Internet connection. Parser/unit checks cannot
substitute for that operation. The existing `visudo` validation is mandatory on
the Pi even though this cloud machine lacks the parser.


## Recovery from the official runner's Node link rejection

The earlier verifier rejected every `..` component in a link target. GitHub's
bundled Node/npm links legitimately use `../lib/...` from `externals/node*/bin`.
The corrected verifier resolves those parent-relative symlinks and archive-root
hardlinks, tracking ancestor aliases at their physical destinations. It rejects
out-of-root/absolute targets, cycles, link pivots, special files, and hardlinks to
symlink inodes; SHA256 verification stays mandatory.

The fix was validated against GitHub's official ARM64 v2.330.0 archive after
checking its published SHA256, plus adversarial synthetic archive tests. No
runner binaries were executed during investigation.

Download the updated `bootstrap/activate-nexarm-runner.py` from GitHub main and
replace only your local activation-script copy. Run it again as pi. Completed
host preparation is detected and verified using matching root-owned files,
runner groups, the existing preservation guard and the read-only broker probe.
The host payload itself is unchanged; no broker, account, allowlist, guard or
service configuration is overwritten. The reported archive failure occurs
before creation of the runner extraction directory, so this retry can proceed
from your completed host stage. If a separate later attempt created an
unconfigured partial runner directory, the script still stops for explicit
inspection rather than overwriting it. No robot container change is required.


## Retry after registration left an unconfigured extraction

The updated script recognizes the extracted official package plus exactly the
registration side-effect locations `.env`, `.path` and `_diag`. Before cleanup
it requires an inactive/failed service, no other runner-account process, unchanged
package hashes/link targets matching the freshly SHA256-verified release, safe
ownership, no mount points, and no configured/credential markers. Any `.runner*`,
`.credentials*` or `.service` marker protects the directory regardless of contents
or file type. Unknown or modified files are preserved for manual review. If the
latest release differs from the extracted version, comparison also fails closed.

Only `/var/lib/nexarm-runner/actions-runner` is removed, using symlink-resistant
removal as the dedicated non-root account. It is then recreated and freshly
extracted before requesting a new registration token. The host broker, account,
allowlist, preservation guard and Docker container are never replaced or removed.
The earlier blanket refusal of all unconfigured directories is superseded only
for this verified failed-registration residue. Configured runners remain protected.


## Upgrade the sudo-compatible runner service

On systemd 252/254, the old unit's `PrivateDevices`, kernel/seccomp restrictions,
`RestrictSUIDSGID` and `RestrictAddressFamilies` implicitly force NoNewPrivs=1 even
though NoNewPrivileges=no is written. This prevents the three existing approved
sudo broker operations. The fix removes those forcing settings and uses
DevicePolicy=closed, filesystem/home isolation and existing resource bounds.
Neither sudoers nor the broker is broadened; nexarm-runner still has no general
sudo, Docker or robot-device group membership. No capabilities are granted.

As **pi**, download the updated standalone bootstrap and run it again when no
Actions job is running. For a configured runner, no registration or extraction
cleanup occurs. It recognizes only the exact historical service unit, verifies
root ownership, refuses local drop-ins/custom units, backs up the old unit to
`/etc/systemd/system/nexarm-actions.service.nexarm-v1.bak`, and atomically replaces
only that unit. It reloads systemd and restarts only the idle runner service.
Broker, sudoers, allowlist, account, guard and nexarm stay unchanged. A job that
appears before restart defers the operation; retry safely when idle. It does not
stop jobs or reset unknown service overrides to force success.

The new service checks the live NoNewPrivs kernel flag and runs only the approved
read-only broker probe in ExecStartPre. If either fails, the runner will not accept
jobs; inspect its journal without relaxing policy. A retry of an already updated
unit also detects an old live process with NoNewPrivs=1 and applies the pending
idle restart. After applying, rerun Pi harmless verification. Its own process
checks NoNewPrivs=0, verifies no direct Docker access, and checks unapproved sudo
commands in **policy-list mode**, without executing them.

This fix has been prepared/tested in the cloud, not applied to the Pi. Service
parser tests substitute missing sudo/runner executables locally; actual startup
and broker/PAM behavior are validated by the Pi-side ExecStartPre and next job.
