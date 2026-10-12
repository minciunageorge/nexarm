#!/usr/bin/env bash
# One-time HOST preparation only. No runner download, registration or activation.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run this reviewed host installer with sudo' >&2; exit 1; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for tool in /usr/bin/python3 /usr/bin/docker visudo useradd sudo; do command -v "$tool" >/dev/null; done
for path in /etc/nexarm-runner /usr/local/lib/nexarm-runner /usr/local/sbin/nexarm-readonly /etc/sudoers.d/nexarm-readonly /etc/systemd/system/nexarm-actions.service; do
  [[ ! -e $path && ! -L $path ]] || { echo "Preserving existing $path; inspect previous setup first" >&2; exit 1; }
done
if id nexarm-runner >/dev/null 2>&1; then
  echo 'Existing nexarm-runner account requires review; nothing overwritten' >&2; exit 1
fi
visudo -cf "$script_dir/runner/readonly.sudoers"
# Only info/inspect against Docker, plus the existing pi-host preservation marker.
sudo -H -u pi bash "$script_dir/bootstrap-preserve.sh"
container_id=$(cat /home/pi/.local/share/nexarm/protect-existing)
[[ $container_id =~ ^[0-9a-f]{64}$ ]]
useradd --system --user-group --create-home --home-dir /var/lib/nexarm-runner --shell /usr/sbin/nologin nexarm-runner
chmod 700 /var/lib/nexarm-runner
install -d -o root -g root -m 755 /usr/local/lib/nexarm-runner
install -d -o root -g root -m 700 /etc/nexarm-runner
install -o root -g root -m 644 "$script_dir/runner/readonly.py" /usr/local/lib/nexarm-runner/readonly.py
install -o root -g root -m 755 "$script_dir/runner/nexarm-readonly" /usr/local/sbin/nexarm-readonly
# ID is a verified non-secret immutable Docker identity, not shell-generated code.
printf '{"container_id":"%s","artifact_paths":[]}\n' "$container_id" >/etc/nexarm-runner/readonly.json
chmod 600 /etc/nexarm-runner/readonly.json
install -o root -g root -m 440 "$script_dir/runner/readonly.sudoers" /etc/sudoers.d/nexarm-readonly
install -o root -g root -m 644 "$script_dir/runner/nexarm-actions.service" /etc/systemd/system/nexarm-actions.service
printf 'Host broker/account prepared; existing nexarm unchanged. Runner is NOT registered, enabled or started.\n'
