#!/usr/bin/env bash
# Run by pi via SSH: checks Docker read-only, writes only a host-side safety guard.
set -euo pipefail
[[ $(id -un) == pi ]] || { echo 'Run as pi, without sudo' >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || { echo '64-bit ARM Linux required' >&2; exit 1; }
for tool in docker tar flock; do command -v "$tool" >/dev/null; done
docker info --format 'Docker={{.OSType}}/{{.Architecture}}'
[[ $(docker inspect -f '{{.State.Running}}' nexarm) == true ]] || {
  echo 'Expected running nexarm container; no changes made' >&2; exit 1;
}
container_id=$(docker inspect -f '{{.Id}}' nexarm)
[[ $container_id =~ ^[0-9a-f]{64}$ ]] || { echo 'Invalid container ID' >&2; exit 1; }
umask 077
base="$HOME/.local/share/nexarm"
mkdir -p "$base"
# Exclusive creation: repeat runs do not overwrite an existing guard.
if [[ -e $base/protect-existing ]]; then
  [[ $(cat "$base/protect-existing") == "$container_id" ]] || {
    echo 'Guard references a different container; investigate manually' >&2; exit 1;
  }
else
  (set -o noclobber; printf '%s\n' "$container_id" >"$base/protect-existing")
fi
printf 'Protected running nexarm: %s\n' "$container_id"
printf 'Guard: %s/protect-existing\n' "$base"
printf 'Container unchanged. Collection is enabled; deployment/camera execution remain blocked.\n'
