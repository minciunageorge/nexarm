# One-time bootstrap: preserve the running Raspberry Pi

For the final laptop-independent setup, use [unattended operation](unattended-operation.md),
which replaces the manual tunnel below with a Pi-owned boot service and relay.
The manual LAN procedure remains useful for initial access and diagnostics.

Target: `pi@192.168.100.153`, Raspberry Pi 5, existing running container `nexarm`.
Run this procedure **on your trusted Linux/macOS laptop or runner with LAN access**,
from a copy of this updated repository. The supplied bootstrap archive can be
extracted with `tar -xzf nexarm-bootstrap.tar.gz`, then `cd nexarm-bootstrap`.
It contains the scripts and documentation, without Git history or credentials;
use a full Git checkout for future development/deployment.
No cloud-to-private-IP route is assumed.
The CLI transfers its helper script through SSH on each request; no repository,
Python, agent, ROS package, or deployment daemon needs installation on the Pi.

The only required Pi-side write is a small host-side protection marker:
`/home/pi/.local/share/nexarm/protect-existing`. Existing SSH keys can be reused.
Nothing below stops, restarts, renames, recreates, relabels, executes commands in,
or changes files/configuration inside the existing container. No robot motion,
camera driver startup, Docker daemon changes, reboot, or package installation.

## 1. Verify the existing SSH identity and noninteractive access

If not already verified, obtain the Pi's SSH host fingerprint through the Pi's
local console or another trusted channel:

```sh
# On the Pi console; displays a public host-key fingerprint only:
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Verify that your existing `192.168.100.153` known-hosts entry matches this identity.
Do not blindly accept a changed host key or treat ssh-keyscan output as trusted.
On the laptop/runner:

```sh
ssh -T -o StrictHostKeyChecking=yes -o BatchMode=yes pi@192.168.100.153 true
```

If this succeeds, reuse that authentication; no new key is required. If your
working login currently requires a password, provision a dedicated key once:

```sh
# Choose a new filename; do not overwrite an existing key.
ssh-keygen -t ed25519 -f "$HOME/.ssh/nexarm_pi" -C nexarm-automation
# Use a passphrase and your existing SSH agent for interactive operation.
ssh-copy-id -i "$HOME/.ssh/nexarm_pi.pub" -o StrictHostKeyChecking=yes pi@192.168.100.153
ssh-add "$HOME/.ssh/nexarm_pi"
ssh -T -i "$HOME/.ssh/nexarm_pi" -o StrictHostKeyChecking=yes -o BatchMode=yes pi@192.168.100.153 true
```

Key enrollment only adds authorization to `pi`'s SSH account. It does not touch
Docker or the container. Keep the private key on the trusted runner, outside Git;
never send it in chat. Unattended execution needs a securely provisioned key/agent
available at runtime; an encrypted key without an unlocked agent cannot automate.
`pi` with Docker access has broad host privileges, so use a trusted runner only.

## 2. Install the preservation guard once

From the updated repository on the laptop/runner:

```sh
ssh -T -o StrictHostKeyChecking=yes -o BatchMode=yes pi@192.168.100.153 \
  'bash -s' < deploy/bootstrap-preserve.sh
```

For the optional dedicated key, add `-i "$HOME/.ssh/nexarm_pi"` to SSH commands.
The helper checks `pi`, ARM64, existing Docker access and running `nexarm`, then
records its existing ID in a mode-600 host file. It only uses Docker `info` and
`inspect`. Repeat runs preserve the same marker; a different container ID fails.
If Docker access or a prerequisite is missing, stop and report the exact error;
do not use sudo, change groups, install Docker, or restart services as a workaround.

Expected output includes `Protected running nexarm: <existing ID>` and
`Container unchanged`. The marker makes this updated framework refuse `deploy`,
`rollback`, and `camera --apply`, even for a managed container. It is a guard
against accidental framework use, not an SSH authorization boundary: arbitrary
Docker access or an older copy of the CLI can bypass it. Keep this updated copy
on the runner and do not use old deployment scripts.

## 3. Establish the existing CLI's SSH tunnel endpoint

On the LAN laptop/runner, keep this command running in one terminal:

```sh
ssh -NT -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:2222:127.0.0.1:22 pi@192.168.100.153
```

This binds only on the runner's loopback interface and forwards SSH to the Pi.
It changes no SSH server configuration. In the runner's `~/.ssh/config`, add this
new block only if `nexarm-tunnel` is unused; preserve all existing entries:

```sshconfig
Host nexarm-tunnel
    HostName 127.0.0.1
    Port 2222
    User pi
    HostKeyAlias 192.168.100.153
    StrictHostKeyChecking yes
    BatchMode yes
```

`HostKeyAlias` reuses the verified real Pi host identity instead of trusting an
unverified loopback endpoint. With a dedicated key, add
`IdentityFile ~/.ssh/nexarm_pi` to this block and pass that key to the outer tunnel
command too. `--identity` applies to the CLI connection, not the tunnel process.

In a second terminal, from the repository:

```sh
python3 tools/nexarm.py probe --host nexarm-tunnel --port 2222
python3 tools/nexarm.py collect-existing --host nexarm-tunnel --port 2222
```

Expected probe output includes `Protection guard active`. Collection saves a
timestamped archive under local `artifacts/`, containing container status and
up to 5,000 Docker log lines from the last 24 hours. It does not require the
framework's managed label or files in `/opt/nexarm`. Docker logs may be empty or
unavailable with the container's existing logging driver; do not change its
logging configuration during bootstrap. A failed collection returns nonzero.

## 4. Add existing ROS logs and camera results when paths are known

Only name paths where files already exist in the current container:

```sh
# Replace the example paths with verified existing paths; they are not defaults.
python3 tools/nexarm.py collect-existing --host nexarm-tunnel --port 2222 \
  --container-path /ACTUAL/ROS/LOG/DIRECTORY \
  --container-path /ACTUAL/SAVED/CAMERA/RESULTS
```

Each `--container-path` copies an existing file/directory using read-only Docker
`cp`; it does not run code in the container. `paths.tsv` maps `path-1`, `path-2`,
etc. to the original paths. Root/traversal paths are rejected. A missing requested
path fails collection and deletes the incomplete local archive, so use the
log-only command until paths are confirmed. Keep requests scoped to result
folders; do not copy credentials, the entire filesystem, or very large caches.
Active logs can change during copying; archives are not application snapshots.

The actual ROS log and camera paths are currently unknown. Start with this
read-only discovery command through the tunnel:

```sh
python3 tools/nexarm.py discover-existing --host nexarm-tunnel --port 2222
```

It prints only the configured container user, working directory, declared `HOME`
and `ROS_LOG_DIR` if present, and bind/volume source-to-container paths. It uses
Docker `inspect` only, never `exec`, and does not dump the complete environment
or secret values. An empty configured user usually means image-default root;
application launch code can still switch users or override ROS_LOG_DIR, so the
output gives candidates rather than proof of active log locations.

ROS often uses `~/.ros/log` for the running process user when ROS_LOG_DIR is not
set. No path is automatically chosen. Match existing result mounts against the
current application's documentation. On the Pi, only after identifying a
specific result mount source, this read-only listing can find candidate files:

```sh
# Replace /ACTUAL/RESULT/MOUNT/SOURCE with the inspected host-side result directory.
# Do not run this against /, /dev, /proc, /sys or a broad mount containing secrets.
find /ACTUAL/RESULT/MOUNT/SOURCE -xdev -maxdepth 3 -type f \
  \( -iname '*.log' -o -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.ppm' \) \
  -print | head -n 100
```

This finds only existing regular files, follows no symlinks, and starts no
process in the container. Execute it manually over your existing trusted SSH
session. A named Docker volume may not be readable by pi on the host; do not
change ownership or permissions. Once a **specific container-side candidate** is
known, list it using Docker's read-only archive output:

```sh
# On the laptop/runner, substitute a specific narrow result directory:
set -o pipefail
ssh -T -o StrictHostKeyChecking=yes -o BatchMode=yes pi@192.168.100.153 \
  'docker cp nexarm:/ACTUAL/CONTAINER/RESULT/DIRECTORY -' | tar -tf -
```

This streams that entire directory's archive to the runner for listing without
extracting or changing it. Use only a small, known result directory; do not scan
the whole container. A missing candidate produces an error and leaves the
container unchanged. If no candidate can be identified from metadata or the
application documentation, report the uncertainty instead of running container
shells, starting camera nodes, or searching unrelated data. Collection of known
paths uses `--container-path` above; no additional Pi installation is needed.

This procedure **collects images already produced by the current application**.
It does not create new camera captures. If images are not currently being saved,
new capture requires a separately authorized observer/integration step after
reviewing the actual ROS network, domain, image topic and image encoding.
Do not use the scaffold's `camera --apply` on the current container: it expects
new-image helper files and writes camera results inside that container.

## 5. Automate collection without more Pi setup

Run the same `collect-existing` command from your trusted scheduler/runner while
the secure route and SSH agent are available. Each run has a unique local artifact
name and a nonzero exit status on failure. Store private keys in the runner's
approved secret store, use verified host keys, and retain archives deliberately.
No CI deployment secret or GitHub self-hosted runner is installed on the Pi.
For unattended tunnels, supervise the same SSH command on the trusted endpoint;
processes do not survive reboot by themselves. Do not daemonize by changing the
robot's existing services during this procedure.

For access from outside the LAN, a route is still required. The local forwarding
above does not make the private Pi reachable from cloud. If an existing VPN or
bastion is available, prefer it. Without one, the following optional pattern needs
an operator-supplied relay hostname/user and separately provisioned relay access:

```sh
# On the Pi (or an already-trusted LAN gateway forwarding to the Pi):
# Pi opens an outbound authenticated relay connection. Foreground, no service install.
ssh -NT -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -R 127.0.0.1:2222:127.0.0.1:22 RELAY_USER@RELAY_HOST

# On the remote runner, after independently verifying the relay's host key:
ssh -NT -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:2222:127.0.0.1:2222 RELAY_USER@RELAY_HOST
```

For a LAN gateway instead of the Pi, the reverse target is
`192.168.100.153:22`, not `127.0.0.1:22`. Relay port 2222 must be unused;
its SSH policy must permit these forwards and keep the reverse listener bound
to loopback. Use dedicated relay authentication with forwarding permissions
restricted to this route; never open public Pi SSH/Docker ports or enable
GatewayPorts. On the remote runner, install the same `nexarm-tunnel` block and
the independently verified Pi public host key, then use the same CLI commands.
Two tunnels and their credentials must remain available. This is a template,
not a claim that a relay exists or cloud connectivity has been established.

## Future deployment: deliberately deferred

SSH transport is reusable for deployment, but the existing container is protected.
Do not remove the marker, relabel it, use `--replace-managed`, or execute deployment
as part of bootstrap. A later authorized migration must inventory the current
image/launch behavior, mounts, devices, networking, ROS domain, secrets and
recovery plan before changing anything. Removing the marker alone does not adopt
an unmanaged container; the original managed-container check remains in force.
No deployment or motion command is part of this procedure.
