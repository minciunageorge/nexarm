# Preferred unattended architecture: GitHub runner on the Pi

**Technically suitable and preferred for this privately controlled project.**
GitHub supports Linux ARM64 self-hosted runners. A Raspberry Pi 5 with a supported
64-bit Linux distribution can maintain an outbound HTTPS connection to GitHub,
receive selected jobs, collect existing logs/images, and upload private artifacts.
No public IP, inbound forwarding, VPS, SSH tunnel, or online Windows laptop is
needed. The Pi still needs continuous power, Internet access and enough storage.

GitHub's official [runner requirements](https://docs.github.com/en/actions/reference/runners/self-hosted-runners)
and [self-hosted runner security guidance](https://docs.github.com/en/actions/reference/security/secure-use)
are the authority. ARM64 and outbound HTTPS/443 support were checked against the
current [GitHub-maintained documentation source](https://github.com/github/docs/blob/main/content/actions/reference/runners/self-hosted-runners.md).
Check the actual Pi distribution/version and current runner requirements before
activation; architecture alone is insufficient. No live Pi inspection occurred.

## Comparison

| Aspect | Pi self-hosted ARM64 runner | VPS relay + hosted jobs |
| --- | --- | --- |
| Infrastructure | Pi + GitHub | Pi + paid always-on VPS + GitHub |
| Networking | Outbound HTTPS/443 only | Pi outbound SSH + public relay SSH listener |
| Laptop needed after setup | No | No |
| SSH identities/host keys | None for routine jobs | Three identities and pinned host keys |
| ARM64 execution | Native on Pi | Hosted runner or separate build target |
| Resources | Shares Pi RAM, CPU, disk with robot | Most job work runs off robot |
| Security exposure | Persistent workflow agent on robot host | Agent off robot; relay access still privileged via pi |
| New privileges in this phase | Fixed read-only broker only | pi SSH account has broad Docker rights |
| Operational work | Runner maintenance and private workflow governance | VPS, SSH policies, tunnel and credential maintenance |
| Codex interface | GitHub Actions API + artifacts | Same API + artifacts |
| Remote interactive SSH | Not provided | Available through relay when authorized |

The direct runner is simpler for bounded collection and later carefully reviewed
updates. Keep the relay as an inactive alternative if interactive administration
is required or workflows must be isolated from the robot host. The proposed runner
is not a general remote shell endpoint or a fully isolated environment.

## Prepared security boundary

The runner uses a dedicated `nexarm-runner` account, **not pi, root, sudo-group or
Docker-group membership**. It gets no SSH keys, robot devices or Docker socket
mounts. Direct Docker access remains unavailable. A root-owned, isolated-Python
helper permits only `probe`, `discover-existing` and `collect-existing`, via three
exact sudoers commands. It accepts no container, arbitrary shell, file path,
configuration path, or Docker subcommand from the workflow.

The administrator enrolls the existing full `nexarm` container ID once. The helper
fails if that named container changes. It uses only Docker inspect/logs/cp on the
enrolled ID, never exec, start, stop, restart, rename, remove, update or build.
Docker logs are bounded to 5,000 lines from the last 24 hours. Collection includes
only root-reviewed path entries; the initial list is empty. Missing paths fail.
Symlinks/special files are rejected in collected results. A 64 MiB per-file limit
and 64 MiB completed-collection limit reject oversized output; the latter is a
post-copy check, so watch free disk space during copying. No camera node is
started. New captures, ROS commands, deployment, motion and application launch
remain disabled. The previous protection marker is preserved as well.

The systemd unit isolates pi's home, device nodes and host write paths, limits
runner CPU to half a core and RAM to 512 MiB, restarts after network/process loss,
and starts at boot. `NoNewPrivileges=no` is intentional because the exact
read-only sudo helper must elevate; setting it to yes would break collection.
Root-owned code is outside the runner workspace and never imports checkout code.
A runner job can still execute code as its own account, access networks and modify
its own persistent work directory. These restrictions **do not isolate ROS/DDS
network traffic or prove untrusted workflow code cannot affect a robot**. Use
trusted private workflows only; no untrusted tests or arbitrary user jobs on Pi.

## Workflow governance is required

Use a **private repository with trusted maintainers only**. If nexarm is public,
register this runner only to a separate private automation repository and copy the
read-only workflow/adapter there. Do not register it to a public repository. A
label selects a runner; it is not an authorization boundary. Other workflows in
a permitted repository can target it, so review all workflows and enforce branch
and repository write protections. Organization runner groups can further scope
repositories/workflows where the GitHub plan supports those controls.

`pi-readonly.yml` is manual/API-dispatch only, gated to main, serialized, with a
ten-minute job timeout and environment `pi-readonly`. No PR, push, schedule,
deployment or motion trigger exists. Actions are pinned to verified full commits;
checkout credentials are not persisted. The job uploads only `artifacts/`, keeps
results for seven days, then removes its result files. Runner workspaces persist:
keep them monitored and do not store unrelated secrets there.

Workflow conditions, environment approval rules and labels alone do not sandbox
a malicious workflow. For routine unattended use, restrict the environment to
main and permit trusted read-only dispatch without a per-run human approval;
review workflow changes separately. Compromised repository write access remains
a serious risk on a robot host. Use a different host for untrusted tests/builds.

## One-time activation procedure (prepared, not executed)

The [single-file activation procedure](activate-pi-runner.md) automates the
Pi-side steps below with local prompts; the detailed steps remain for review.

1. Use the existing trusted LAN SSH access as pi to transfer this updated bundle
   into a new host directory. No files go into the container. Confirm `uname -m`
   is `aarch64` and the OS is currently supported by GitHub's Linux ARM64 runner.
   Do not upgrade the robot OS or Docker as part of this procedure.
2. Create/configure the private GitHub repository, protected main branch and
   `pi-readonly` environment before registering the runner. Publish the reviewed
   workflow and adapter. No repository push/settings changes have been performed
   here. Hosted CI remains responsible for ordinary framework tests.
3. Review and run the host preparation script from the transferred bundle:

   ```sh
   sudo bash deploy/install-pi-runner-host.sh
   ```

   This creates the dedicated account, preservation guard, root-owned broker,
   root-owned empty path allowlist and a stopped systemd unit. It refuses existing
   destination files/accounts rather than overwriting them. It does not download,
   register, enable or start a runner. It uses only Docker info/inspect to enroll
   the existing container. `visudo -cf` is mandatory before installing policy.

4. In the private repository's Settings → Actions → Runners → New self-hosted
   runner, select **Linux / ARM64**. Use its current version-specific official
   download commands and SHA256 verification instructions. Download and extract
   only into `/var/lib/nexarm-runner/actions-runner`, owned by nexarm-runner.
   Run commands as the dedicated user, for example:

   ```sh
   sudo -u nexarm-runner /bin/bash
   cd /var/lib/nexarm-runner
   mkdir actions-runner
   cd actions-runner
   # Run GitHub's current Linux ARM64 download + checksum + extraction commands.
   # Keep TLS and checksum verification enabled. Do not use RUNNER_ALLOW_RUNASROOT.
   # Install only documented missing host dependencies after reviewing their effect.
   ./config.sh --url https://github.com/YOUR_OWNER/YOUR_PRIVATE_REPOSITORY \
     --token ONE_TIME_REGISTRATION_TOKEN --name nexarm-pi5-readonly \
     --labels nexarm-readonly --work _work
   exit
   ```

   Enter the short-lived registration token locally through the GitHub-provided
   flow; do not send it in chat or save it in Git. If using the inline command,
   keep it out of shell history and shared process logs. Do not use a permanent
   PAT as runner registration data. Allow normal runner auto-updates; keeping the
   runner current is necessary to continue accepting jobs. The initial package's
   verification does not imply future downloads were tested here.

5. Validate the prepared service. After the runner is configured, enable only
   this new host service, **not the robot container**:

   ```sh
   sudo systemd-analyze verify /etc/systemd/system/nexarm-actions.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now nexarm-actions.service
   systemctl status nexarm-actions.service --no-pager
   journalctl -u nexarm-actions.service -n 50 --no-pager
   ```

   Use this provided service instead of installing a second service with
   `svc.sh`. The service includes run.sh auto-update/restart behavior. Registration
   may finish but service startup can still fail because of OS libraries,
   hardening or connectivity; inspect and resolve that specific failure without
   changing Docker/robot services. Service directives were checked locally with a harmless substitute executable,
   but the real runner path, boot,
   updater and ARM64 runtime behavior remain unverified on the actual Pi.

6. Confirm GitHub shows the runner online/idle. Dispatch `Pi read-only collection`
   on main with operation `probe`, then `collect-existing`. Verify the archive has
   `status.txt`, `container.log` and `paths.json`. Compare the container ID/running
   state to the existing container. Job success, not an online badge alone, proves
   operational collection. No Pi reboot or robot restart is required.

7. Dispatch `discover-existing` to learn declared log paths/mounts. An administrator
   can later edit `/etc/nexarm-runner/readonly.json` to add **confirmed existing,
   narrow result directories** under `artifact_paths`, preserving container_id
   and root-only ownership/mode 600. This is a host-side allowlist edit; no
   container files or hardware settings change. The workflow cannot supply its
   own paths. Camera images are collected only if already saved by the application.

The Pi needs outbound HTTPS access to GitHub's documented runner, action download,
update and artifact endpoints (including relevant wildcard/CNAME destinations).
Use the current official domain list rather than assuming github.com alone is
enough. DNS/time/TLS must work; do not disable TLS verification. No inbound
router ports are needed. Internet loss queues jobs; an offline runner cannot
execute them, and GitHub's queue limits still apply. Service retries restore
connectivity but do not fix expired registration or broken OS dependencies.

## Day-to-day ChatGPT/Codex interface

Codex dispatches the GitHub workflow, waits for its result, downloads the private
artifact and analyzes it. No SSH/private Pi credential is needed in Codex or
GitHub Actions secrets for this design; runner registration keeps its own GitHub
credentials on the Pi. Do not expose those credential files in artifacts.

A GitHub integration/API binding capable of Actions dispatch, polling and artifact
download is still required in ChatGPT/Codex. It has not been established here.
Git clone authentication is not proof of Actions API authorization. Provision that
access securely, scoped to the private automation repository, without pasting
credentials in chat. The self-hosted runner solves the transport, not this API
integration prerequisite. Temporary GitHub UI dispatch is possible until that
integration exists, with the laptop still unnecessary for the Pi connection.

## Future jobs: extend authority deliberately

The architecture can later support native ARM64 application tests and updates,
but no such jobs are enabled now. Keep build/test work on hosted or separate
runners whenever possible to avoid robot resource contention. Tests on Pi must
use a separately reviewed sandbox with explicit limits, no device access and
isolated networking; do not grant the runner Docker-group membership.

For a future authorized deployment, design a **separate root-owned deployment
broker** with immutable artifact validation, explicitly approved image/revision,
current-container inventory, a rollback plan and a separate protected GitHub
environment. The read-only helper must stay read-only. Review ROS/camera launch
and motion authority separately. Existing deploy scripts and protection markers
are not activated or relaxed by runner installation.

## Current status

Prepared and locally tested; no account/helper/service has been installed on the
Pi, no runner registered, no workflow pushed or dispatched, and no nexarm or
hardware change performed. Tests exercise rejection of non-read operations,
container-ID changes, safe path selection and fixed Docker operations. The local
machine lacks visudo, so its parser test is explicitly skipped; the Pi installer
requires it. Live ARM64/runner/registration/artifact checks remain outstanding.
