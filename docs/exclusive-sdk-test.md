# Prepared exclusive Aurora SDK transaction — unarmed

The user authorizes stopping only the dedicated `aurora930_node`, testing
`SimpleView_FetchFrame` with a timeout, and restoring that same camera node.
Robot/servo control, container replacement/restart, packages, USB resets and
arbitrary commands remain outside scope. This is preparation, not a live test.

[exclusive_sdk.py](../deploy/runner/exclusive_sdk.py) provides the transaction
engine, strict preflight checks, a pinned-process backend and an independent
session guardian. No workflow option, sudoers permission, broker operation or
bootstrap activation has been added. Running the file directly refuses execution.
No prepared example is an enrolled profile.

Before wiring live execution, collect/review the actual SDK discovery artifact:

- Exact full executable paths and SHA256 for `aurora930_node` and
  `SimpleView_FetchFrame`, plus SDK/driver dependencies and sample source/defaults.
- Original driver PID, start ticks, container PID namespace, UID/security context,
  full original argv, working directory and private environment. No secrets go
  into Git, workflow parameters or public artifacts.
- Camera-only process ownership and launch source: no shared components, respawn,
  exit-triggered shutdown of robot nodes, or SDK child processes. A executable
  name or “yes” confirmation alone does not establish these properties.
- Stable protected original ROS parameter files; ephemeral `/tmp/launch_params*`
  are refused because the original launcher can delete them when the node exits.
- SDK sample behavior: no camera reset, device configuration/calibration change,
  GUI dependency, required input, or other side effects; it must support a private
  working directory instead of writing into the original driver/robot workspace. A timeout alone cannot
  make an unreviewed SDK binary safe.

The eventual protected enrollment must keep `enabled` false until those facts
are verified. It is an operator-controlled root-owned profile, never workflow
arguments. The backend refuses UID/security-context changes, PID 1, executable
changes, arbitrary binary names, changed argv/cwd or unresolved restoration.
Dependency/source hashes and durable enrollment must be completed in the live
broker wiring after the actual installed SDK is identified. This module is not
production enrollment or a ready-to-run Pi command.

The prepared sequence is:

1. Validate enrollment and both protected executable hashes before any stop.
   Open a pidfd and verify PID start ticks/executable to avoid PID-reuse signals.
2. Capture original argv/cwd/environment privately. Stop only the pinned driver
   with SIGTERM, wait five seconds, then refuse the SDK if that camera executable
   is still running or has respawned. Never signal launchers, robot nodes, process
   groups or the whole container; do not escalate SIGKILL on the existing driver.
3. Run only the enrolled `SimpleView_FetchFrame`, without shell or extra flags,
   with a 1–15 second timeout. Terminate then kill only the newly started test
   process if necessary. SDK samples that fork are unsupported. Logs and test output
   stay in a private transaction directory, with an 8 MiB per-file cap, 128-entry
   and 64 MiB aggregate output guard; nothing is automatically published.
4. In a `finally` path, restore the original executable/argv/cwd/environment and
   signal defaults. Never create a duplicate when the original process remains;
   unexpected respawn is reported for reconciliation. Never restore while a stuck
   SDK process still holds the camera. A restored process must survive startup.
5. The guardian owns the complete transaction in a separate session, so caller
   cancellation does not bypass restoration. It keeps private mode-600 state and
   a lock under `/tmp/nexarm-exclusive-sdk`; unresolved state blocks another test.
   SIGKILL of the guardian, host reboot, uninterruptible USB IO or hardware failure
   cannot have an unconditional restoration guarantee. Failure is explicit.
6. Before activation, integrate automatic `aurora-health` and one RGB frame checks
   after restoration, and preserve SDK logs/images in bounded private artifacts.
   A surviving process alone is not verified camera health. That integration is
   pending verified live enrollment and is intentionally not enabled now.

Tests use a fake backend and verify guards, success/failure/timeout restoration,
refusal before a stop, and explicit restore failure. No SDK or camera is executed
by these tests. The backend/guardian must also receive isolated real-process
validation once the actual executable/launcher assumptions have been verified.

**No Pi bootstrap rerun is required for this preparation.** Keep the existing
read-only discovery available, and provide the artifact/path/source evidence
before authorizing the live wiring. The existing runner/container remains intact.
