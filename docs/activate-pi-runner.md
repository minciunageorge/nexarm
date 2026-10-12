# Minimal activation from your current pi SSH session

No VPS/tunnel is needed. The downloadable **standalone activation script embeds
all broker, sudoers, protection and service files**, so no repository clone or
multi-command host installer is required on the Pi.

## Before running

Use a private GitHub repository with trusted workflows and protected `main`.
Publish the reviewed project changes, including `pi-readonly.yml`, `pi-verify.yml`
and their Python adapters, to that repository's main branch. These commits have
not been pushed by Codex. Create the GitHub environment `pi-readonly`, restricted
to main. The script asks for the private owner/repository and uses your confirmation;
it does not treat a public/private lookup failure as proof of privacy.

If nexarm is public, use a separate private automation repository containing the
reviewed workflow/adapter files. Do not enroll a robot-hosted runner in a public
repository. This initial GitHub-side setup cannot be replaced by a Pi command
without separately provisioned repository administration credentials.

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

1. The private repository (`minciunageorge/nexarm` is suggested, not assumed private).
2. A typed `PRIVATE` confirmation of your repository/workflow policy.
3. GitHub's short-lived **runner registration token** from Settings → Actions →
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

In the private repository's Actions tab, select **Pi harmless verification**,
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
- Uploads a seven-day private verification artifact and removes job result files.

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
