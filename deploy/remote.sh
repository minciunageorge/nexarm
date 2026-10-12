#!/usr/bin/env bash
set -euo pipefail
operation=$1
revision=${2:-}
replace=${3:-no}
network=${4:-none}
base="$HOME/.local/share/nexarm"
assert_unprotected() {
  [[ ! -e $base/protect-existing ]] || {
    echo 'Existing nexarm is protected: deployment, rollback and camera execution are disabled. Use collect-existing.' >&2
    exit 1
  }
}
managed() { [[ $(docker inspect -f '{{index .Config.Labels "org.nexarm.managed"}}' nexarm) == true ]]; }
exists() { docker inspect nexarm >/dev/null 2>&1; }
run_image() {
  docker run -d --name nexarm --label org.nexarm.managed=true     --restart no --read-only --cap-drop ALL --security-opt no-new-privileges     --pids-limit 256 --memory 2g --cpus 2 --user "$(id -u):$(id -g)"     --network "$network" --env ROS_LOCALHOST_ONLY="$([[ $network == host ]] && echo 0 || echo 1)"     --env ROS_DOMAIN_ID=42 --tmpfs /tmp:rw,nosuid,nodev,size=256m     --mount "type=bind,src=$base/results,dst=/results" "$1"
}
case "$operation" in
probe)
  [[ $(uname -m) == aarch64 ]] || { echo 'A 64-bit ARM OS is required' >&2; exit 1; }
  docker info --format '{{.OSType}}/{{.Architecture}}'
  if [[ -e $base/protect-existing ]]; then echo 'Protection guard active'; fi
  if exists; then docker inspect -f 'Existing nexarm managed={{index .Config.Labels "org.nexarm.managed"}}' nexarm; fi
  ;;
deploy|rollback)
  assert_unprotected
  [[ $network == none || $network == host ]]
  [[ $(uname -m) == aarch64 ]]
  docker info >/dev/null
  # Serialize changes, including builds and rollback.
  mkdir -p "$base/results/ros" "$base/results/camera"
  exec 9>"$base/deploy.lock"; flock -n 9 || { echo 'Deployment in progress' >&2; exit 1; }
  old=''
  if exists; then
    managed || { echo 'Refusing to replace unmanaged nexarm container' >&2; exit 1; }
    [[ $replace == yes ]] || { echo 'Use --replace-managed to replace nexarm' >&2; exit 1; }
    [[ $(docker inspect -f '{{.State.Running}}' nexarm) == true ]] || { echo 'Existing container must be running to provide rollback' >&2; exit 1; }
    old=$(docker inspect -f '{{.Image}}' nexarm)
  fi
  if [[ $operation == deploy ]]; then
    [[ $revision =~ ^[0-9a-f]{40}$ ]]
    image="nexarm:$revision"
    context=$(mktemp -d)
    trap 'rm -rf "$context"' EXIT
    tar -xf - -C "$context"
    docker build --platform linux/arm64 -t "$image" "$context"
  else
    [[ -s $base/previous-image ]] || { echo 'No previous successful image' >&2; exit 1; }
    image=$(cat "$base/previous-image")
  fi
  # Keep the old container itself for exact configuration rollback.
  if [[ -n $old ]]; then
    ! docker inspect nexarm-rollback >/dev/null 2>&1 || { echo 'Resolve existing nexarm-rollback container first' >&2; exit 1; }
    docker rename nexarm nexarm-rollback
  fi
  success=no
  restore() {
    if [[ $success != yes ]]; then
      docker rm -f nexarm >/dev/null 2>&1 || true
      if [[ -n $old ]]; then docker rename nexarm-rollback nexarm; docker start nexarm; fi
    fi
    if [[ -n ${context:-} ]]; then rm -rf "$context"; fi
  }
  trap restore EXIT
  if [[ -n $old ]]; then docker stop --time 20 nexarm-rollback >/dev/null; fi
  run_image "$image"
  docker exec nexarm /opt/nexarm/entrypoint.sh python3 -c 'import rclpy; rclpy.init(); n=rclpy.create_node("nexarm_smoke"); assert n.get_name()=="nexarm_smoke"; n.destroy_node(); rclpy.shutdown()' >"$base/results/smoke.txt" 2>&1
  [[ $(docker inspect -f '{{.State.Running}}' nexarm) == true ]]
  success=yes
  if [[ -n $old ]]; then printf '%s\n' "$old" >"$base/previous-image"; docker rm nexarm-rollback >/dev/null; fi
  ;;
discover-existing)
  # Only selected path-related metadata. Never dump .Config.Env or credentials.
  docker inspect -f 'user={{.Config.User}} workdir={{.Config.WorkingDir}}{{println}}{{range .Config.Env}}{{if ge (len .) 12}}{{if eq (slice . 0 12) "ROS_LOG_DIR="}}{{println .}}{{end}}{{end}}{{if ge (len .) 5}}{{if eq (slice . 0 5) "HOME="}}{{println .}}{{end}}{{end}}{{end}}' nexarm
  docker inspect -f '{{range .Mounts}}{{println "mount" .Type "source=" .Source "container=" .Destination}}{{end}}' nexarm
  ;;
collect-existing)
  # Read only Docker API operations; never exec, stop, relabel or write in nexarm.
  temp=$(mktemp -d)
  trap 'rm -rf "$temp"' EXIT
  docker inspect -f 'id={{.Id}} image={{.Image}} running={{.State.Running}} started={{.State.StartedAt}}' nexarm >"$temp/status.txt"
  docker logs --timestamps --since 24h --tail 5000 nexarm >"$temp/container.log" 2>&1
  shift 4
  index=0
  for path in "$@"; do
    [[ $path == /* && $path != / && $path != *$'\n'* && $path != *$'\r'* ]] || { echo 'Invalid artifact path' >&2; exit 2; }
    case "/${path#/}/" in */../*) echo 'Path traversal rejected' >&2; exit 2;; esac
    index=$((index+1))
    printf 'path-%s\t%s\n' "$index" "$path" >>"$temp/paths.tsv"
    # docker cp reads the source; does not follow source symlinks with -L.
    docker cp "nexarm:$path" "$temp/path-$index"
  done
  tar -czf - -C "$temp" .
  ;;
collect)
  managed || { echo 'Container is not managed by nexarm' >&2; exit 1; }
  temp=$(mktemp -d)
  trap 'rm -rf "$temp"' EXIT
  docker logs --timestamps nexarm >"$temp/container.log" 2>&1
  docker inspect -f 'image={{.Image}} running={{.State.Running}} exit={{.State.ExitCode}}' nexarm >"$temp/status.txt"
  cp -a "$base/results" "$temp/results"
  docker cp nexarm:/opt/nexarm/test-results "$temp/test-results"
  docker cp nexarm:/opt/nexarm/ws/log "$temp/colcon-log" 2>/dev/null || true
  tar -czf - -C "$temp" .
  ;;
camera)
  assert_unprotected
  managed
  docker exec nexarm /opt/nexarm/entrypoint.sh python3 /opt/nexarm/camera_capture.py --topic "$revision" --output /results/camera/latest.ppm
  ;;
*) exit 2 ;;
esac
