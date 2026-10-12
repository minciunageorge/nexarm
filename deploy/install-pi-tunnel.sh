#!/usr/bin/env bash
# Explicit one-time Pi HOST setup. Never executes commands in a robot container.
set -euo pipefail
[[ $# == 1 ]] || { echo 'Usage: bash deploy/install-pi-tunnel.sh /path/to/rendered/pi' >&2; exit 2; }
[[ $(id -un) == pi && $HOME == /home/pi ]] || { echo 'Run as pi with HOME=/home/pi, not sudo' >&2; exit 1; }
source_dir=$(realpath "$1")
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ssh_dir=/home/pi/.ssh/nexarm
unit=/etc/systemd/system/nexarm-tunnel.service
for tool in ssh ssh-keygen systemctl systemd-analyze sudo; do command -v "$tool" >/dev/null; done
for file in relay_ed25519 relay_known_hosts; do
  [[ -f $ssh_dir/$file && ! -L $ssh_dir/$file ]] || { echo "Provision $ssh_dir/$file first" >&2; exit 1; }
  [[ $(stat -c '%U' "$ssh_dir/$file") == pi ]] || { echo 'SSH files must be owned by pi' >&2; exit 1; }
done
[[ $(stat -c '%a' "$ssh_dir/relay_ed25519") == 600 ]] || { echo 'Private key must have mode 600' >&2; exit 1; }
# Boot cannot depend on an interactive passphrase/agent. Public output is discarded.
ssh-keygen -y -P '' -f "$ssh_dir/relay_ed25519" >/dev/null
ssh-keygen -F nexarm-relay-infra -f "$ssh_dir/relay_known_hosts" >/dev/null
ssh -G -F "$source_dir/relay.conf" nexarm-relay >/dev/null
systemd-analyze verify "$source_dir/nexarm-tunnel.service"
# Preserve existing service/config files. A change requires explicit operator review.
for pair in "relay.conf:$ssh_dir/relay.conf" "nexarm-tunnel.service:$unit"; do
  name=${pair%%:*}; destination=${pair#*:}
  if [[ -e $destination || -L $destination ]]; then
    [[ ! -L $destination ]] && cmp -s "$source_dir/$name" "$destination" || {
      echo "Refusing to overwrite $destination; inspect existing setup" >&2; exit 1;
    }
  fi
done
# Writes only the Pi-host protection marker; Docker calls are info/inspect only.
bash "$script_dir/bootstrap-preserve.sh"
install -m 600 "$source_dir/relay.conf" "$ssh_dir/relay.conf"
sudo install -o root -g root -m 644 "$source_dir/nexarm-tunnel.service" "$unit"
sudo systemctl daemon-reload
sudo systemctl enable --now nexarm-tunnel.service
systemctl is-enabled nexarm-tunnel.service
systemctl is-active nexarm-tunnel.service
printf 'Pi outbound service installed. Verify end-to-end probe; active alone does not prove connectivity.\n'
