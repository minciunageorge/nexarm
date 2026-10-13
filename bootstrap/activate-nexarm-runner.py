#!/usr/bin/env python3
"""One-time Pi activation; generated standalone copy embeds reviewed host files."""
import getpass
from collections import deque
from pathlib import PurePosixPath
import hashlib
import json
import sys
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

PAYLOAD = {'deploy/bootstrap-preserve.sh': '#!/usr/bin/env bash\n# Run by pi via SSH: checks Docker read-only, writes only a host-side safety guard.\nset -euo pipefail\n[[ $(id -un) == pi ]] || { echo \'Run as pi, without sudo\' >&2; exit 1; }\n[[ $(uname -m) == aarch64 ]] || { echo \'64-bit ARM Linux required\' >&2; exit 1; }\nfor tool in docker tar flock; do command -v "$tool" >/dev/null; done\ndocker info --format \'Docker={{.OSType}}/{{.Architecture}}\'\n[[ $(docker inspect -f \'{{.State.Running}}\' nexarm) == true ]] || {\n  echo \'Expected running nexarm container; no changes made\' >&2; exit 1;\n}\ncontainer_id=$(docker inspect -f \'{{.Id}}\' nexarm)\n[[ $container_id =~ ^[0-9a-f]{64}$ ]] || { echo \'Invalid container ID\' >&2; exit 1; }\numask 077\nbase="$HOME/.local/share/nexarm"\nmkdir -p "$base"\n# Exclusive creation: repeat runs do not overwrite an existing guard.\nif [[ -e $base/protect-existing ]]; then\n  [[ $(cat "$base/protect-existing") == "$container_id" ]] || {\n    echo \'Guard references a different container; investigate manually\' >&2; exit 1;\n  }\nelse\n  (set -o noclobber; printf \'%s\\n\' "$container_id" >"$base/protect-existing")\nfi\nprintf \'Protected running nexarm: %s\\n\' "$container_id"\nprintf \'Guard: %s/protect-existing\\n\' "$base"\nprintf \'Container unchanged. Collection is enabled; deployment/camera execution remain blocked.\\n\'\n', 'deploy/install-pi-runner-host.sh': '#!/usr/bin/env bash\n# One-time HOST preparation only. No runner download, registration or activation.\nset -euo pipefail\n[[ $(id -u) == 0 ]] || { echo \'Run this reviewed host installer with sudo\' >&2; exit 1; }\nscript_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)\nfor tool in /usr/bin/python3 /usr/bin/docker visudo useradd sudo; do command -v "$tool" >/dev/null; done\nfor path in /etc/nexarm-runner /usr/local/lib/nexarm-runner /usr/local/sbin/nexarm-readonly /etc/sudoers.d/nexarm-readonly /etc/systemd/system/nexarm-actions.service; do\n  [[ ! -e $path && ! -L $path ]] || { echo "Preserving existing $path; inspect previous setup first" >&2; exit 1; }\ndone\nif id nexarm-runner >/dev/null 2>&1; then\n  echo \'Existing nexarm-runner account requires review; nothing overwritten\' >&2; exit 1\nfi\nvisudo -cf "$script_dir/runner/readonly.sudoers"\n# Only info/inspect against Docker, plus the existing pi-host preservation marker.\nsudo -H -u pi bash "$script_dir/bootstrap-preserve.sh"\ncontainer_id=$(cat /home/pi/.local/share/nexarm/protect-existing)\n[[ $container_id =~ ^[0-9a-f]{64}$ ]]\nuseradd --system --user-group --create-home --home-dir /var/lib/nexarm-runner --shell /usr/sbin/nologin nexarm-runner\nchmod 700 /var/lib/nexarm-runner\ninstall -d -o root -g root -m 755 /usr/local/lib/nexarm-runner\ninstall -d -o root -g root -m 700 /etc/nexarm-runner\nfor helper in container_diagnostics.py; do\n  install -o root -g root -m 644 "$script_dir/runner/$helper" "/usr/local/lib/nexarm-runner/$helper"\ndone\ninstall -o root -g root -m 644 "$script_dir/runner/readonly.py" /usr/local/lib/nexarm-runner/readonly.py\ninstall -o root -g root -m 755 "$script_dir/runner/nexarm-readonly" /usr/local/sbin/nexarm-readonly\n# ID is a verified non-secret immutable Docker identity, not shell-generated code.\nprintf \'{"container_id":"%s","artifact_paths":[]}\\n\' "$container_id" >/etc/nexarm-runner/readonly.json\nchmod 600 /etc/nexarm-runner/readonly.json\ninstall -o root -g root -m 440 "$script_dir/runner/readonly.sudoers" /etc/sudoers.d/nexarm-readonly\ninstall -o root -g root -m 644 "$script_dir/runner/nexarm-actions.service" /etc/systemd/system/nexarm-actions.service\nprintf \'Host broker/account prepared; existing nexarm unchanged. Runner is NOT registered, enabled or started.\\n\'\n', 'deploy/runner/readonly.py': '#!/usr/bin/python3\n"""Root-installed, fixed-scope Docker read broker. Never import checkout code."""\nimport json\nimport base64\nimport os\nfrom pathlib import Path\nimport re\nimport resource\nimport subprocess\nimport sys\nimport tarfile\nimport tempfile\n\nCONFIG = Path(\'/etc/nexarm-runner/readonly.json\')\nDOCKER = \'/usr/bin/docker\'\nOPERATIONS = (\'probe\', \'discover-existing\', \'collect-existing\', \'ros-inspect\', \'camera-one-frame\', \'aurora-health\', \'aurora-discover\', \'aurora-recover\')\n\n\ndef validate(config):\n    identifier = config[\'container_id\']\n    if not re.fullmatch(r\'[0-9a-f]{64}\', identifier):\n        raise ValueError(\'Invalid enrolled container ID\')\n    paths = config.get(\'artifact_paths\', [])\n    if not isinstance(paths, list) or len(paths) > 8:\n        raise ValueError(\'Invalid artifact path list\')\n    for path in paths:\n        if not isinstance(path, str) or not path.startswith(\'/\') or path == \'/\' or \'..\' in Path(path).parts or \'\\n\' in path:\n            raise ValueError(\'Invalid artifact path\')\n        if Path(path).parts[1] in (\'dev\', \'proc\', \'sys\', \'etc\'):\n            raise ValueError(\'System paths are not result directories\')\n    return identifier, paths\n\n\ndef docker(arguments, output=None):\n    command = [DOCKER, \'--host\', \'unix:///var/run/docker.sock\', *arguments]\n    result = subprocess.run(command, stdout=output or subprocess.PIPE, stderr=subprocess.PIPE,\n                            timeout=10 if arguments[0]==\'inspect\' else 120, env={\'PATH\': \'/usr/bin:/bin\', \'HOME\': \'/nonexistent\', \'LANG\': \'C.UTF-8\'})\n    if result.returncode:\n        raise RuntimeError(\'Docker read operation failed; confirm container and enrolled paths\')\n    return result.stdout\n\n\ndef host_usb_inventory():\n    """Fixed sysfs attributes only; never open USB, reset, unbind or read serials."""\n    root=Path(\'/sys/bus/usb/devices\')\n    if not root.exists():return {\'visible\':False,\'devices\':[]}\n    entries=sorted(root.iterdir(),key=lambda path:path.name)\n    if len(entries)>256:raise ValueError(\'Host USB inventory limit exceeded\')\n    devices=[]\n    for path in entries:\n        try:\n            vendor=(path/\'idVendor\').read_text()[:16].strip()\n            product=(path/\'idProduct\').read_text()[:16].strip()\n        except OSError:continue\n        if not re.fullmatch(\'[0-9a-fA-F]{4}\',vendor) or not re.fullmatch(\'[0-9a-fA-F]{4}\',product):continue\n        item={\'sysfs_name\':path.name,\'vendor_id\':vendor,\'product_id\':product}\n        for key in (\'manufacturer\',\'product\',\'authorized\',\'busnum\',\'devnum\',\'speed\',\'bConfigurationValue\'):\n            try:\n                with (path/key).open() as source:item[key]=source.read(128).strip()\n            except OSError:pass\n        interfaces=[]\n        for interface in entries:\n            if interface.name.startswith(path.name+\':\'):\n                try:driver=Path(os.readlink(interface/\'driver\')).name\n                except OSError:driver=None\n                interfaces.append({\'interface\':interface.name,\'driver\':driver})\n        item[\'interfaces\']=interfaces\n        devices.append(item)\n    return {\'visible\':True,\'devices\':devices,\'scope\':\'Presence/authorization/interface binding only; no transfer or reset\'}\n\n\ndef timeout_diagnostics(data):\n    """Copy only bounded fixed-topic numeric/QoS data from the observer."""\n    result=json.loads(data)[\'camera_timeout\']\n    if not isinstance(result,dict): raise ValueError(\'Invalid timeout diagnostics\')\n    if result.get(\'topic\')!=\'/depth_cam/rgb/image_raw\': raise ValueError(\'Unexpected diagnostic topic\')\n    count=result[\'publisher_count\']\n    if type(count) is not int or not 0<=count<=200: raise ValueError(\'Invalid publisher count\')\n    clean={\'topic\':result[\'topic\'],\'publisher_count\':count}\n    for field,allowed in [(\'offered_reliability\',{\'RELIABLE\',\'BEST_EFFORT\',\'SYSTEM_DEFAULT\',\'UNKNOWN\'}),\n                          (\'offered_durability\',{\'VOLATILE\',\'TRANSIENT_LOCAL\',\'SYSTEM_DEFAULT\',\'UNKNOWN\'})]:\n        values=result[field]\n        if not isinstance(values,list) or len(values)>200 or any(type(v) is not str or v not in allowed for v in values):\n            raise ValueError(\'Invalid offered QoS\')\n        clean[field]=values\n    attempts=result[\'attempts\']\n    if not isinstance(attempts,list) or not 1<=len(attempts)<=2: raise ValueError(\'Invalid attempt count\')\n    clean[\'attempts\']=[]\n    for index,attempt in enumerate(attempts):\n        if not isinstance(attempt,dict): raise ValueError(\'Invalid delivery attempt\')\n        reliability=(\'RELIABLE\',\'BEST_EFFORT\')[index]\n        if attempt.get(\'reliability\')!=reliability or attempt.get(\'durability\')!=\'VOLATILE\':\n            raise ValueError(\'Invalid requested QoS\')\n        item={\'reliability\':reliability,\'durability\':\'VOLATILE\'}\n        for field in (\'matched_publishers\',\'max_matched_publishers\',\'messages_received\'):\n            value=attempt[field]\n            if type(value) is not int or not 0<=value<=200: raise ValueError(\'Invalid delivery count\')\n            item[field]=value\n        clean[\'attempts\'].append(item)\n    return json.dumps(clean)\n\n\ndef discovery_failure(operation, errors, exit_code=None, timed_out=False):\n    """Bounded real stderr/exit cause for the fixed discovery/report operations."""\n    errors.seek(0,2);size=errors.tell();errors.seek(0)\n    if size>65536:\n        first=errors.read(16384);errors.seek(-49152,2)\n        reason=first+b\'\\n[stderr truncated: middle omitted]\\n\'+errors.read(49152)\n    else:reason=errors.read(65536)\n    signal_number = -exit_code if exit_code is not None and exit_code<0 else (exit_code-128 if exit_code is not None and 128<exit_code<=192 else None)\n    return json.dumps({\'operation\':operation, \'status\':\'failed\', \'exit_code\':exit_code,\n                       \'possible_signal\':signal_number, \'docker_client_timed_out\':timed_out,\n                       \'timeout_seconds\':25 if timed_out else None,\n                       \'stderr_bytes\':size,\'stderr_truncated\':size>65536,\n                       \'stderr\':reason.decode(\'utf-8\',errors=\'replace\')})\n\n\ndef observe(operation, identifier):\n    # No caller-controlled command/topic/UID/env. No helper copied into container.\n    script=Path(__file__).with_name(\'container_diagnostics.py\').read_bytes()\n    command=[DOCKER,\'--host\',\'unix:///var/run/docker.sock\',\'exec\',\'-i\',\'--user\',\'65534:65534\',\n             \'--env\',\'HOME=/tmp\',\'--env\',\'ROS_LOG_DIR=/tmp\',\n             \'--env\',\'LD_PRELOAD=\',\'--env\',\'LD_LIBRARY_PATH=/opt/ros/humble/lib\',\n             \'--env\',\'AMENT_PREFIX_PATH=/opt/ros/humble\',\'--env\',\'RCUTILS_LOGGING_USE_STDOUT=0\',\n             identifier,\'/usr/bin/python3\',\'-I\',\'-\',operation]\n    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:\n        try:\n            completed=subprocess.run(command,input=script,stdout=output,stderr=errors,timeout=25,check=False,\n                           env={\'PATH\':\'/usr/bin:/bin\',\'HOME\':\'/nonexistent\',\'LANG\':\'C.UTF-8\'},\n                           preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE,(4*1024*1024,4*1024*1024)))\n        except subprocess.TimeoutExpired:\n            if operation in (\'aurora-discover\',\'aurora-recover\'):\n                raise ValueError(discovery_failure(operation,errors,timed_out=True)) from None\n            raise\n        if completed.returncode:\n            if operation in (\'aurora-discover\',\'aurora-recover\'):\n                raise ValueError(discovery_failure(operation,errors,exit_code=completed.returncode))\n            errors.seek(0); reason=errors.read(4096)\n            details=None\n            if operation==\'camera-one-frame\':\n                output.seek(0)\n                try:\n                    details=timeout_diagnostics(output.read(64*1024+1))\n                except (ValueError,KeyError,TypeError):\n                    pass\n            if details is not None or b\'TimeoutError: No Aurora RGB frame\' in reason:\n                raise ValueError(\'No Aurora RGB frame within 15 seconds; \'+\n                                 (details or \'No valid bounded delivery diagnostics returned\'))\n            if b\'Expected a 640x400\' in reason:\n                raise ValueError(\'Existing camera frame has unsupported dimensions or encoding\')\n            if b\'ModuleNotFoundError\' in reason or b\'ImportError\' in reason:\n                raise ValueError(\'Existing container lacks compatible Humble Python modules; no packages were installed\')\n            raise ValueError(\'Fixed ROS observer failed; check Humble Python 3.10, existing publisher and container DDS access\')\n        output.seek(0); data=output.read(4*1024*1024+1)\n    if len(data)>4*1024*1024: raise ValueError(\'Observer output limit exceeded\')\n    result=json.loads(data)\n    if operation in (\'aurora-discover\',\'aurora-recover\'):\n        if result.get(\'operation\')!=operation or result.get(\'recovery\',{}).get(\'enabled\') is not False or result.get(\'recovery\',{}).get(\'status\')!=\'refused\':\n            raise ValueError(\'Discovery cannot enable camera recovery\')\n        try:\n            result[\'host_usb\']=host_usb_inventory()\n        except (OSError,ValueError) as error:\n            result[\'host_usb\']={\'visible\':None,\'devices\':[],\'error_type\':type(error).__name__,\'error\':str(error)[:4096]}\n            result[\'status\']=\'partial\'\n        print(json.dumps(result))\n    elif operation==\'aurora-health\':\n        expected={\'rgb\':\'/depth_cam/rgb/image_raw\',\'depth\':\'/depth_cam/depth/image_raw\',\'ir\':\'/depth_cam/ir/image_raw\'}\n        streams=result.get(\'streams\')\n        if result.get(\'operation\')!=\'aurora-health\' or not isinstance(streams,dict) or set(streams)!=set(expected):\n            raise ValueError(\'Malformed Aurora health result\')\n        if any(not isinstance(streams[name],dict) or streams[name].get(\'topic\')!=topic for name,topic in expected.items()):\n            raise ValueError(\'Unexpected Aurora health topic\')\n        print(json.dumps(result))\n    elif operation==\'ros-inspect\':\n        if not isinstance(result.get(\'nodes\'),list) or not isinstance(result.get(\'topics\'),list):\n            raise ValueError(\'Malformed ROS graph result\')\n        if len(result[\'nodes\'])>200 or len(result[\'topics\'])>200: raise ValueError(\'Graph limit exceeded\')\n        print(json.dumps(result))\n    else:\n        if result.get(\'topic\')!=\'/depth_cam/rgb/image_raw\' or (result.get(\'width\'),result.get(\'height\'))!=(640,400) or result.get(\'encoding\')!=\'rgb8\':\n            raise ValueError(\'Unexpected camera result\')\n        pixels=base64.b64decode(result.pop(\'pixels\'),validate=True)\n        if len(pixels)!=640*400*3: raise ValueError(\'Unexpected frame length\')\n        with tempfile.TemporaryDirectory() as temporary:\n            root=Path(temporary)\n            (root/\'aurora-rgb.ppm\').write_bytes(b\'P6\\n640 400\\n255\\n\'+pixels)\n            (root/\'camera.json\').write_text(json.dumps(result,indent=2)+\'\\n\')\n            with tarfile.open(fileobj=sys.stdout.buffer,mode=\'w|gz\') as archive:\n                for path in root.iterdir(): archive.add(path,arcname=path.name)\n\n\ndef execute(operation, config):\n    if operation not in OPERATIONS:\n        raise ValueError(\'Only read-only operations are enabled\')\n    if operation in (\'ros-inspect\', \'camera-one-frame\', \'aurora-health\', \'aurora-discover\', \'aurora-recover\') and config.get(\'diagnostics_enabled\') is not True:\n        raise ValueError(\'ROS/camera diagnostics are not enrolled\')\n    identifier, paths = validate(config)\n    current = docker([\'inspect\', \'-f\', \'{{.Id}}\', \'nexarm\']).decode().strip()\n    if current != identifier:\n        raise ValueError(\'Container identity changed; administrator review required\')\n    if operation in (\'ros-inspect\',\'camera-one-frame\',\'aurora-health\',\'aurora-discover\',\'aurora-recover\'):\n        before=docker([\'inspect\',\'-f\',\'{{.Id}} {{.State.Running}} {{.State.StartedAt}}\',identifier])\n        observe(operation,identifier)\n        if before!=docker([\'inspect\',\'-f\',\'{{.Id}} {{.State.Running}} {{.State.StartedAt}}\',identifier]):\n            raise ValueError(\'Container state changed during observation\')\n    elif operation == \'probe\':\n        sys.stdout.buffer.write(docker([\'inspect\', \'-f\', \'id={{.Id}} running={{.State.Running}} image={{.Image}} started={{.State.StartedAt}}\', identifier]))\n    elif operation == \'discover-existing\':\n        template = \'user={{.Config.User}} workdir={{.Config.WorkingDir}}{{println}}{{range .Config.Env}}{{if ge (len .) 12}}{{if eq (slice . 0 12) "ROS_LOG_DIR="}}{{println .}}{{end}}{{end}}{{if ge (len .) 5}}{{if eq (slice . 0 5) "HOME="}}{{println .}}{{end}}{{end}}{{end}}\'\n        sys.stdout.buffer.write(docker([\'inspect\', \'-f\', template, identifier]))\n        sys.stdout.buffer.write(docker([\'inspect\', \'-f\', \'{{range .Mounts}}{{println .Type .Source .Destination}}{{end}}\', identifier]))\n    else:\n        with tempfile.TemporaryDirectory(prefix=\'nexarm-readonly-\') as temporary:\n            root = Path(temporary)\n            (root / \'status.txt\').write_bytes(docker([\'inspect\', \'-f\', \'id={{.Id}} running={{.State.Running}} image={{.Image}} started={{.State.StartedAt}}\', identifier]))\n            command = [DOCKER, \'--host\', \'unix:///var/run/docker.sock\', \'logs\', \'--timestamps\', \'--since\', \'24h\', \'--tail\', \'5000\', identifier]\n            with (root / \'container.log\').open(\'wb\') as logs:\n                subprocess.run(command, stdout=logs, stderr=subprocess.STDOUT, timeout=120, check=True,\n                               env={\'PATH\': \'/usr/bin:/bin\', \'HOME\': \'/nonexistent\', \'LANG\': \'C.UTF-8\'})\n            for index, path in enumerate(paths, 1):\n                docker([\'cp\', f\'{identifier}:{path}\', str(root / f\'path-{index}\')])\n            (root / \'paths.json\').write_text(json.dumps(paths, indent=2)+\'\\n\')\n            total = 0\n            for path in root.rglob(\'*\'):\n                if path.is_symlink() or not (path.is_file() or path.is_dir()):\n                    raise ValueError(\'Special files/symlinks not permitted in enrolled artifacts\')\n                if path.is_file():\n                    total += path.stat().st_size\n            if total > 64 * 1024 * 1024:\n                raise ValueError(\'Collection exceeds 64 MiB; narrow enrolled result paths\')\n            with tarfile.open(fileobj=sys.stdout.buffer, mode=\'w|gz\') as archive:\n                for path in sorted(root.iterdir()):\n                    archive.add(path, arcname=path.name, recursive=True)\n\n\ndef main():\n    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in OPERATIONS:\n        raise SystemExit(\'Only fixed approved diagnostic operations are enabled\')\n    # Installed config is root-owned; never honor caller environment/config paths.\n    info = CONFIG.stat()\n    if info.st_uid != 0 or info.st_mode & 0o022 or CONFIG.is_symlink():\n        raise SystemExit(\'Unsafe installed configuration ownership/permissions\')\n    try:\n        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))\n        execute(sys.argv[1], json.loads(CONFIG.read_text()))\n    except ValueError as error:\n        raise SystemExit(str(error)) from None\n    except (KeyError, RuntimeError, OSError, subprocess.SubprocessError):\n        raise SystemExit(\'Broker failed closed: verify enrollment, container identity and observer timeout\') from None\n\n\nif __name__ == \'__main__\':\n    main()\n', 'deploy/runner/nexarm-readonly': '#!/bin/sh\n# -I ignores caller PYTHONPATH/site packages; root-owned code only.\nexec /usr/bin/python3 -I /usr/local/lib/nexarm-runner/readonly.py "$@"\n', 'deploy/runner/readonly.sudoers': 'Defaults:nexarm-runner env_reset\nnexarm-runner ALL=(root) NOPASSWD: /usr/local/sbin/nexarm-readonly probe, /usr/local/sbin/nexarm-readonly discover-existing, /usr/local/sbin/nexarm-readonly collect-existing, /usr/local/sbin/nexarm-readonly ros-inspect, /usr/local/sbin/nexarm-readonly camera-one-frame, /usr/local/sbin/nexarm-readonly aurora-health, /usr/local/sbin/nexarm-readonly aurora-discover, /usr/local/sbin/nexarm-readonly aurora-recover\n', 'deploy/runner/nexarm-actions.service': '[Unit]\nDescription=Nexarm GitHub ARM64 read-only runner (no deployment)\nWants=network-online.target\nAfter=network-online.target\nStartLimitIntervalSec=0\n\n[Service]\nUser=nexarm-runner\nGroup=nexarm-runner\nWorkingDirectory=/var/lib/nexarm-runner/actions-runner\n# Test the kernel flag and the exact read-only sudo path INSIDE this sandbox.\nExecStartPre=/usr/bin/python3 -I -c \'import pathlib; flag = next(line for line in pathlib.Path("/proc/self/status").read_text().splitlines() if line.startswith("NoNewPrivs:")); assert flag.split()[1] == "0", "Service sandbox forbids approved read-only sudo"\'\nExecStartPre=/usr/bin/sudo -n /usr/local/sbin/nexarm-readonly probe\nExecStart=/var/lib/nexarm-runner/actions-runner/run.sh\nRestart=always\nRestartSec=15\nKillMode=control-group\nTimeoutStopSec=60\nUMask=0077\nStandardInput=null\nProtectSystem=strict\nProtectHome=yes\nReadWritePaths=/var/lib/nexarm-runner\nPrivateTmp=yes\nProtectControlGroups=yes\n# Seccomp-based hardening can implicitly force NNP on systemd 252/254.\n# Do not re-add it here or grant capabilities to bypass it. Cgroup device access\n# and filesystem isolation remain restricted without disabling setuid sudo.\nDevicePolicy=closed\n# sudoers still permits ONLY the three fixed read-only broker commands.\nNoNewPrivileges=no\nTasksMax=128\nMemoryMax=512M\nCPUQuota=50%\n\n[Install]\nWantedBy=multi-user.target\n', 'deploy/runner/container_diagnostics.py': '"""Fixed transient ROS observer. No shell, actuator publishers, driver launch or image-file output."""\nimport base64\nimport ctypes\nimport faulthandler\nimport json\nimport os\nfrom pathlib import Path\nimport re\nimport resource\nimport signal\nimport sys\nimport time\nimport traceback\n\n# Default SIGALRM terminates even if middleware/native code blocks; killing the\n# Docker client alone would otherwise leave docker exec running in the container.\nfaulthandler.register(signal.SIGALRM, file=sys.stderr, all_threads=False, chain=True)\nsignal.alarm(22)\nresource.setrlimit(resource.RLIMIT_AS, (512*1024*1024,512*1024*1024))\nresource.setrlimit(resource.RLIMIT_CPU, (15,15))\nif sys.version_info[:2] != (3,10):\n    raise SystemExit(\'Expected ROS Humble Python 3.10\')\nfor path in [\'/opt/ros/humble/local/lib/python3.10/dist-packages\',\'/opt/ros/humble/lib/python3.10/site-packages\']:\n    sys.path.insert(0,path)\nimport rclpy\nfrom rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy\nfrom rclpy.utilities import get_rmw_implementation_identifier\nfrom sensor_msgs.msg import Image\n\nRGB_TOPIC = \'/depth_cam/rgb/image_raw\'\n# Primary reader matches the live Aurora RELIABLE/VOLATILE writer. A bounded\n# best-effort fallback preserves support for sensor-data writers on this topic.\nCAMERA_QOS = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=5,\n                        reliability=ReliabilityPolicy.RELIABLE,\n                        durability=DurabilityPolicy.VOLATILE)\nFALLBACK_QOS = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=5,\n                          reliability=ReliabilityPolicy.BEST_EFFORT,\n                          durability=DurabilityPolicy.VOLATILE)\n\n\n# Humble\'s Python Subscription wrapper lacks get_publisher_count(). Borrow the\n# protected rcl_subscription_t pointer for this fixed read-only RCL query.\n_rcl = ctypes.CDLL(\'/opt/ros/humble/lib/librcl.so\')\n_count_matched = _rcl.rcl_subscription_get_publisher_count\n_count_matched.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]\n_count_matched.restype = ctypes.c_int\n\n\ndef matched_publishers(subscription):\n    count = ctypes.c_size_t()\n    handle = subscription.handle\n    with handle:\n        status = _count_matched(ctypes.c_void_p(handle.pointer), ctypes.byref(count))\n    if status != 0:\n        raise RuntimeError(\'RCL matched-publisher query failed\')\n    return count.value\n\n\ndef describe_qos(qos):\n    return {\'reliability\': qos.reliability.name, \'durability\': qos.durability.name,\n            \'history\': qos.history.name, \'depth\': qos.depth,\n            \'deadline_ns\': qos.deadline.nanoseconds,\n            \'lifespan_ns\': qos.lifespan.nanoseconds,\n            \'liveliness\': qos.liveliness.name,\n            \'liveliness_lease_duration_ns\': qos.liveliness_lease_duration.nanoseconds}\n\n\ndef camera_publishers():\n    endpoints = node.get_publishers_info_by_topic(RGB_TOPIC)\n    if len(endpoints) > 200:\n        raise RuntimeError(\'Camera publisher limit exceeded\')\n    return {\'topic\': RGB_TOPIC, \'publisher_count\': node.count_publishers(RGB_TOPIC),\n            \'publishers\': [{\'node_name\': endpoint.node_name,\n                            \'node_namespace\': endpoint.node_namespace,\n                            \'topic_type\': endpoint.topic_type,\n                            \'offered_qos\': describe_qos(endpoint.qos_profile)}\n                           for endpoint in endpoints],\n            \'requested_qos\': describe_qos(CAMERA_QOS),\n            \'fallback_requested_qos\': describe_qos(FALLBACK_QOS)}\n\n\nHEALTH_TOPICS = {\'rgb\': RGB_TOPIC, \'depth\': \'/depth_cam/depth/image_raw\',\n                 \'ir\': \'/depth_cam/ir/image_raw\'}\n\n\ndef aurora_health():\n    # Only these three exact Image topics. No parameters, launch, USB/device\n    # queries, driver counters or actuator publishers. Image pixels are discarded.\n    streams = {}\n    subscriptions = []\n    started = time.monotonic()\n    try:\n        for channel, topic in HEALTH_TOPICS.items():\n            readers = []\n            streams[channel] = {\'topic\': topic, \'readers\': readers}\n            for reliability in (ReliabilityPolicy.RELIABLE, ReliabilityPolicy.BEST_EFFORT):\n                stats = {\'reliability\': reliability.name, \'durability\': \'VOLATILE\',\n                         \'messages_received\': 0, \'first_stamp\': None, \'last_stamp\': None,\n                         \'stamp_advances\': 0, \'stamp_repeats\': 0, \'stamp_regressions\': 0,\n                         \'first_arrival_ms\': None, \'last_arrival_ms\': None,\n                         \'max_matched_publishers\': 0, \'matched_publishers\': 0}\n                readers.append(stats)\n                def receive(message, stats=stats):\n                    stamp = [message.header.stamp.sec, message.header.stamp.nanosec]\n                    arrival = round((time.monotonic()-started)*1000)\n                    if stats[\'messages_received\']:\n                        previous = stats[\'last_stamp\']\n                        field = \'stamp_advances\' if stamp > previous else \'stamp_repeats\' if stamp == previous else \'stamp_regressions\'\n                        stats[field] += 1\n                    else:\n                        stats[\'first_stamp\'] = stamp\n                        stats[\'first_arrival_ms\'] = arrival\n                    stats[\'messages_received\'] += 1\n                    stats[\'last_stamp\'] = stamp\n                    stats[\'last_arrival_ms\'] = arrival\n                qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1,\n                                 reliability=reliability, durability=DurabilityPolicy.VOLATILE)\n                subscription = node.create_subscription(Image, topic, receive, qos)\n                subscriptions.append((subscription, stats))\n        deadline = started+10\n        while time.monotonic() < deadline:\n            rclpy.spin_once(node, timeout_sec=0.1)\n            for subscription, stats in subscriptions:\n                matched = matched_publishers(subscription)\n                stats[\'matched_publishers\'] = matched\n                stats[\'max_matched_publishers\'] = max(stats[\'max_matched_publishers\'], matched)\n        for channel, stream in streams.items():\n            count = node.count_publishers(stream[\'topic\'])\n            stream[\'publisher_count\'] = count\n            readers = stream[\'readers\']\n            stream[\'messages_observed\'] = any(reader[\'messages_received\'] for reader in readers)\n            stream[\'counter_advancing\'] = any(reader[\'messages_received\'] >= 2 for reader in readers)\n            stream[\'timestamp_advancing\'] = any(reader[\'stamp_advances\'] for reader in readers)\n            stream[\'status\'] = (\'messages_observed\' if stream[\'messages_observed\'] else\n                                \'matched_no_messages\' if any(reader[\'max_matched_publishers\'] for reader in readers) else\n                                \'discovered_unmatched\' if count else \'no_publisher_discovered\')\n        other_flow = streams[\'depth\'][\'messages_observed\'] or streams[\'ir\'][\'messages_observed\']\n        summary = (\'rgb_silent_other_streams_delivering\' if not streams[\'rgb\'][\'messages_observed\'] and other_flow else\n                   \'all_observed_streams_silent\' if not any(s[\'messages_observed\'] for s in streams.values()) else\n                   \'messages_observed\')\n        return {\'operation\': \'aurora-health\', \'observation_ms\': round((time.monotonic()-started)*1000),\n                \'ros_domain_id\': os.environ.get(\'ROS_DOMAIN_ID\',\'0\'),\n                \'rmw\': get_rmw_implementation_identifier(), \'streams\': streams,\n                \'summary\': summary, \'counters_are\': \'observer_callbacks_per_reader_not_driver_counters\'}\n    finally:\n        for subscription, _ in subscriptions:\n            node.destroy_subscription(subscription)\n\n\nOFFICIAL_PACKAGE = \'deptrum-ros-driver-aurora930\'\nOFFICIAL_LAUNCH = \'aurora930_launch.py\'\nCAMERA_NODES = (\'/aurora\', \'/depth_cam\')\n\n\ndef safe_text(path, limit=4096):\n    try:\n        with path.open(\'rb\') as source:\n            return source.read(limit).decode(\'utf-8\', errors=\'replace\')\n    except OSError:\n        return None\n\n\ndef process_inventory():\n    records = {}\n    # No environ, full command line, descriptors or memory contents exported.\n    # Arguments are examined only for the known launch signature/remap hints.\n    entries = sorted((p for p in Path(\'/proc\').iterdir() if p.name.isdecimal()), key=lambda p:int(p.name))\n    if len(entries)>1024: raise RuntimeError(\'Process inventory limit exceeded\')\n    for path in entries:\n        status = safe_text(path/\'status\')\n        cmdline = safe_text(path/\'cmdline\', 16384)\n        if status is None: continue\n        fields = dict(line.split(\':\',1) for line in status.splitlines() if \':\' in line)\n        arguments = (cmdline or \'\').split(\'\\x00\')\n        official = any(i>0 and Path(arguments[i-1]).name==\'ros2\' and arguments[i:i+3]==[\'launch\',OFFICIAL_PACKAGE,OFFICIAL_LAUNCH]\n                       for i in range(max(0,len(arguments)-2)))\n        try:\n            executable = os.readlink(path/\'exe\')\n        except OSError:\n            executable = None\n        basename = Path(executable).name if executable else fields.get(\'Name\',\'\').strip()\n        component = basename in (\'component_container\',\'component_container_mt\',\'component_container_isolated\')\n        camera_hint = official or any(\'deptrum\' in value.lower() or \'aurora930\' in value.lower()\n                                     for value in [basename,*(arguments[:2])])\n        camera_hint = camera_hint or any(value in (\'__node:=depth_cam\',\'__node:=aurora\') for value in arguments)\n        try:\n            record = {\'pid\':int(path.name), \'ppid\':int(fields.get(\'PPid\',\'0\')),\n                      \'uid\':int(fields.get(\'Uid\',\'-1\').split()[0]),\n                      \'state\':fields.get(\'State\',\'unknown\').strip()[:64],\n                      \'threads\':int(fields.get(\'Threads\',\'0\')),\n                      \'name\':fields.get(\'Name\',\'\').strip()[:64],\n                      \'executable\':executable, \'official_launch\':official,\n                      \'component_container\':component, \'camera_hint\':camera_hint,\n                      \'argv_readable\':cmdline is not None, \'node_pid_mapping_proven\':False}\n        except ValueError: continue\n        record[\'wait_channel\']=safe_text(path/\'wchan\',64)\n        raw_stat=safe_text(path/\'stat\',4096)\n        if raw_stat:\n            try:record[\'start_ticks\']=int(raw_stat.rsplit(\')\',1)[1].split()[19])\n            except (ValueError,IndexError):pass\n        if official:record[\'launch_signature\']=[\'ros2\',\'launch\',OFFICIAL_PACKAGE,OFFICIAL_LAUNCH]\n        records[record[\'pid\']] = record\n    selected = {pid for pid,record in records.items() if record[\'camera_hint\'] or record[\'component_container\']}\n    # Include descendants, bounded by the same inventory cap; never signal them.\n    for _ in range(len(records)):\n        descendants={pid for pid,record in records.items() if record[\'ppid\'] in selected}\n        if descendants<=selected:break\n        selected.update(descendants)\n    if len(selected)>64: raise RuntimeError(\'Camera process candidate limit exceeded\')\n    return [records[pid] for pid in sorted(selected)]\n\n\ndef usb_inventory():\n    devices=[]\n    root=Path(\'/sys/bus/usb/devices\')\n    if not root.exists():return {\'visible\':False,\'devices\':[]}\n    entries=sorted(root.iterdir(),key=lambda path:path.name)\n    if len(entries)>256:raise RuntimeError(\'USB inventory limit exceeded\')\n    for path in entries:\n        vendor=safe_text(path/\'idVendor\',16);product=safe_text(path/\'idProduct\',16)\n        if vendor is None or product is None:continue\n        vendor=vendor.strip();product=product.strip()\n        if not re.fullmatch(\'[0-9a-fA-F]{4}\',vendor) or not re.fullmatch(\'[0-9a-fA-F]{4}\',product):continue\n        info={\'sysfs_name\':path.name,\'vendor_id\':vendor,\'product_id\':product}\n        for key in (\'manufacturer\',\'product\',\'authorized\',\'busnum\',\'devnum\',\'speed\',\'bConfigurationValue\'):\n            value=safe_text(path/key,128)\n            if value is not None:info[key]=value.strip()\n        try:info[\'driver\']=Path(os.readlink(path/\'driver\')).name\n        except OSError:info[\'driver\']=None\n        devices.append(info)\n    return {\'visible\':True,\'devices\':devices,\n            \'limitations\':\'Presence/authorized/binding only; no transfer, reset, unbind or kernel-log access\'}\n\n\ndef device_visibility():\n    root=Path(\'/dev/bus/usb\');devices=[]\n    if root.exists():\n        for bus in sorted(root.iterdir())[:64]:\n            if not bus.name.isdecimal() or not bus.is_dir():continue\n            for path in sorted(bus.iterdir())[:128]:\n                if not path.name.isdecimal():continue\n                info=path.stat()\n                devices.append({\'path\':str(path),\'uid\':info.st_uid,\'gid\':info.st_gid,\n                                \'mode\':oct(info.st_mode & 0o777),\n                                \'observer_readable\':os.access(path,os.R_OK),\n                                \'observer_writable\':os.access(path,os.W_OK)})\n                if len(devices)>=128: return {\'devices\':devices,\'truncated\':True}\n    return {\'devices\':devices,\'truncated\':False}\n\n\ndef shared_memory_visibility():\n    # Metadata only, for diagnosing a root-owned writer vs UID 65534 DDS reader.\n    root=Path(\'/dev/shm\');items=[]\n    if not root.exists():return []\n    entries=sorted(root.iterdir(),key=lambda path:path.name)\n    if len(entries)>256:raise RuntimeError(\'Shared memory inventory limit exceeded\')\n    for path in entries:\n        if not path.name.startswith((\'fastrtps\',\'fastdds\')):continue\n        try:info=path.lstat()\n        except OSError:continue\n        items.append({\'name\':path.name,\'uid\':info.st_uid,\'gid\':info.st_gid,\n                      \'mode\':oct(info.st_mode & 0o777),\'mtime_ns\':info.st_mtime_ns,\n                      \'observer_readable\':os.access(path,os.R_OK),\n                      \'observer_writable\':os.access(path,os.W_OK)})\n    return items\n\n\ndef component_membership(services):\n    managers=[name for name,types in services if \'composition_interfaces/srv/ListNodes\' in types]\n    managers=sorted(set(managers),key=lambda name:(not any(name.startswith(camera+\'/\') for camera in CAMERA_NODES), \'deptrum\' not in name.lower(),name))[:16]\n    if not managers:return []\n    try:\n        from composition_interfaces.srv import ListNodes\n    except ImportError:\n        return [{\'service\':name,\'status\':\'module_unavailable\'} for name in managers]\n    results=[];deadline=time.monotonic()+4\n    for name in managers:\n        if time.monotonic()>=deadline:\n            results.append({\'service\':name,\'status\':\'budget_exhausted\'});continue\n        client=node.create_client(ListNodes,name)\n        try:\n            if not client.wait_for_service(timeout_sec=0.1):\n                results.append({\'service\':name,\'status\':\'unavailable\'});continue\n            future=client.call_async(ListNodes.Request())\n            rclpy.spin_until_future_complete(node,future,timeout_sec=max(0,deadline-time.monotonic()))\n            if not future.done() or future.exception() is not None:\n                results.append({\'service\':name,\'status\':\'timeout_or_error\'});continue\n            response=future.result()\n            if len(response.unique_ids)!=len(response.full_node_names):\n                raise RuntimeError(\'Component membership shape mismatch\')\n            total=len(response.full_node_names)\n            scan=min(total,4096)\n            indexes=sorted(range(scan),key=lambda i:(response.full_node_names[i] not in CAMERA_NODES,i))[:200]\n            results.append({\'service\':name,\'status\':\'truncated\' if total>200 else \'reported\',\n                            \'total_nodes\':total,\'scanned_nodes\':scan,\'nodes_truncated\':total>200,\'scan_truncated\':total>scan,\n                            \'nodes\':[response.full_node_names[i] for i in indexes],\n                            \'component_ids\':[response.unique_ids[i] for i in indexes],\n                            \'includes_known_camera\':any(n in CAMERA_NODES for n in response.full_node_names[:scan]),\n                            \'other_nodes_present\':any(n not in CAMERA_NODES for n in response.full_node_names[:scan])})\n        finally:node.destroy_client(client)\n    return results\n\n\ndef probe_section(name, function, fallback, errors):\n    """Optional probes cannot discard independent discovery evidence."""\n    try:return function()\n    except (OSError,RuntimeError,ValueError,IndexError,AttributeError) as error:\n        errors.append({\'section\':name,\'error_type\':type(error).__name__,\n                       \'error\':str(error)[:4096], \'traceback\':traceback.format_exc(limit=6)[-8192:]})\n        return fallback\n\n\ndef aurora_discover():\n    deadline=time.monotonic()+3\n    while time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=0.1)\n    nodes=node.get_node_names_and_namespaces()\n    services=node.get_service_names_and_types()\n    node_total=len(nodes);service_total=len(services)\n    node_scan=min(node_total,4096);service_scan=min(service_total,4096)\n    nodes=nodes[:node_scan];services=services[:service_scan]\n    manager_matches=sum(\'composition_interfaces/srv/ListNodes\' in types for _,types in services)\n    graph={\'total_nodes\':node_total,\'total_services\':service_total,\n           \'scanned_nodes\':node_scan,\'scanned_services\':service_scan,\n           \'scan_limit\':4096,\'node_scan_truncated\':node_total>node_scan,\n           \'service_scan_truncated\':service_total>service_scan,\n           \'component_manager_matches\':manager_matches,\'component_manager_limit\':16,\n           \'component_managers_truncated\':manager_matches>16}\n    errors=[]\n    truncated=node_total>node_scan or service_total>service_scan or manager_matches>16\n    seen=set()\n    camera_nodes=[]\n    for name,namespace in nodes:\n        full=namespace.rstrip(\'/\')+\'/\'+name\n        if full not in CAMERA_NODES or full in seen:continue\n        seen.add(full)\n        def services_for_node(name=name,namespace=namespace):\n            offered=node.get_service_names_and_types_by_node(name,namespace)\n            if len(offered)>64:raise RuntimeError(\'Camera service limit exceeded\')\n            return offered\n        offered=probe_section(\'camera_services:\'+full,services_for_node,[],errors)\n        unavailable=any(error[\'section\']==\'camera_services:\'+full for error in errors)\n        camera_nodes.append({\'name\':full,\'services\':offered,\n                             \'services_status\':\'unavailable\' if unavailable else \'reported\',\n                             \'has_lifecycle_services\':None if unavailable else any(\'lifecycle_msgs/srv/ChangeState\' in types for _,types in offered)})\n    publishers={}\n    for channel,topic in HEALTH_TOPICS.items():\n        endpoints=node.get_publishers_info_by_topic(topic)\n        if len(endpoints)>64:raise RuntimeError(\'Camera endpoint limit exceeded\')\n        publishers[channel]={\'topic\':topic,\'publisher_count\':node.count_publishers(topic),\n                             \'nodes\':[endpoint.node_namespace.rstrip(\'/\')+\'/\'+endpoint.node_name for endpoint in endpoints]}\n    components=probe_section(\'component_membership\',lambda:component_membership(services),[],errors)\n    truncated=truncated or any(result.get(\'nodes_truncated\',False) for result in components)\n    processes=probe_section(\'processes\',process_inventory,[],errors)\n    usb=probe_section(\'container_usb\',usb_inventory,{\'visible\':None,\'devices\':[]},errors)\n    devices=probe_section(\'container_devices\',device_visibility,{\'devices\':[],\'truncated\':False},errors)\n    shared_memory=probe_section(\'dds_shared_memory\',shared_memory_visibility,[],errors)\n    return {\'operation\':\'aurora-discover\',\'nodes\':camera_nodes,\'camera_publishers\':publishers,\n            \'component_manager_services\':components, \'processes\':processes,\n            \'container_usb\':usb,\'container_devices\':devices,\n            \'observer_uid\':os.geteuid(), \'dds_shared_memory\':shared_memory,\n            \'status\':\'partial\' if errors or truncated else \'complete\', \'probe_errors\':errors,\n            \'graph\':graph, \'truncated\':truncated,\n            \'ros_domain_id\':os.environ.get(\'ROS_DOMAIN_ID\',\'0\'),\'rmw\':get_rmw_implementation_identifier(),\n            \'recovery\':{\'enabled\':False,\'status\':\'refused\',\n                        \'reason\':\'Live process/component ownership and camera-only restart mechanism are not yet proven\',\n                        \'official_launch\':[\'ros2\',\'launch\',OFFICIAL_PACKAGE,OFFICIAL_LAUNCH],\n                        \'container_restart_allowed\':False,\'process_signals_allowed\':False}}\n\n\noperation=sys.argv[1]\nif operation not in (\'ros-inspect\',\'camera-one-frame\',\'aurora-health\',\'aurora-discover\',\'aurora-recover\'): raise SystemExit(\'Unsupported observer operation\')\nrclpy.init()\nnode=rclpy.create_node(\'nexarm_diagnostic_observer\',enable_rosout=False,start_parameter_services=False)\ntry:\n    if operation in (\'aurora-discover\',\'aurora-recover\'):\n        report=aurora_discover()\n        if operation==\'aurora-recover\':\n            report[\'operation\']=\'aurora-recover\'\n            report[\'health\']=aurora_health()\n        print(json.dumps(report),flush=True)\n    elif operation==\'aurora-health\':\n        print(json.dumps(aurora_health()),flush=True)\n    elif operation==\'ros-inspect\':\n        deadline=time.monotonic()+3\n        while time.monotonic()<deadline: rclpy.spin_once(node,timeout_sec=0.1)\n        nodes=node.get_node_names_and_namespaces()\n        topics=node.get_topic_names_and_types()\n        if len(nodes)>200 or len(topics)>200: raise RuntimeError(\'Graph size limit exceeded\')\n        print(json.dumps({\'nodes\':sorted(nodes),\'topics\':sorted(topics), \'ros_domain_id\':os.environ.get(\'ROS_DOMAIN_ID\',\'0\'), \'rmw\':get_rmw_implementation_identifier(), \'camera\':camera_publishers()}),flush=True)\n    else:\n        frame=[]\n        attempts=[]\n        deadline=time.monotonic()+15\n        for qos in (CAMERA_QOS,FALLBACK_QOS):\n            attempt={\'reliability\':qos.reliability.name, \'durability\':qos.durability.name,\n                     \'max_matched_publishers\':0, \'matched_publishers\':0,\n                     \'messages_received\':0}\n            attempts.append(attempt)\n            def receive(message):\n                attempt[\'messages_received\']+=1\n                if not frame: frame.append(message)\n            subscription=node.create_subscription(Image,RGB_TOPIC,receive,qos)\n            try:\n                attempt_deadline=min(deadline,time.monotonic()+8) if qos is CAMERA_QOS else deadline\n                while not frame and time.monotonic()<attempt_deadline:\n                    rclpy.spin_once(node,timeout_sec=0.1)\n                    matched=matched_publishers(subscription)\n                    attempt[\'matched_publishers\']=matched\n                    attempt[\'max_matched_publishers\']=max(attempt[\'max_matched_publishers\'],matched)\n            finally:\n                node.destroy_subscription(subscription)\n            if frame: break\n        publishers=camera_publishers()\n        delivery={\'topic\':RGB_TOPIC, \'publisher_count\':publishers[\'publisher_count\'],\n                  \'offered_reliability\':[p[\'offered_qos\'][\'reliability\'] for p in publishers[\'publishers\']],\n                  \'offered_durability\':[p[\'offered_qos\'][\'durability\'] for p in publishers[\'publishers\']],\n                  \'attempts\':attempts}\n        if not frame:\n            # Broker accepts only this bounded schema and exports it to the error\n            # artifact; raw container stderr/tracebacks are never forwarded.\n            print(json.dumps({\'camera_timeout\':delivery}),flush=True)\n            raise TimeoutError(\'No Aurora RGB frame within 15 seconds\')\n        message=frame[0]\n        if (message.width,message.height)!=(640,400) or message.encoding not in (\'rgb8\',\'bgr8\'):\n            raise ValueError(\'Expected a 640x400 rgb8/bgr8 Aurora frame\')\n        if not 1920<=message.step<=4096 or len(message.data)!=message.step*400:\n            raise ValueError(\'Invalid/oversized image stride\')\n        pixels=bytearray()\n        for y in range(400):\n            row=bytes(message.data[y*message.step:y*message.step+1920])\n            if message.encoding==\'bgr8\':\n                row=bytes(value for x in range(0,len(row),3) for value in (row[x+2],row[x+1],row[x]))\n            pixels.extend(row)\n        print(json.dumps({\'topic\':\'/depth_cam/rgb/image_raw\',\'width\':640,\'height\':400,\n                          \'encoding\':\'rgb8\',\'ros_domain_id\':os.environ.get(\'ROS_DOMAIN_ID\',\'0\'),\'rmw\':get_rmw_implementation_identifier(),\'pixels\':base64.b64encode(pixels).decode(),\n                          \'stamp_sec\':message.header.stamp.sec,\'stamp_nanosec\':message.header.stamp.nanosec,\n                          \'frame_id\':message.header.frame_id[:256], \'camera\':publishers, \'selected_qos\':describe_qos(qos), \'delivery\':delivery}),flush=True)\nfinally:\n    node.destroy_node();rclpy.shutdown()\n', 'deploy/runner/upgrade_diagnostics.py': '#!/usr/bin/python3\n"""Explicit operator-run root upgrade, never authorized to the runner account."""\nimport hashlib\nimport json\nimport os\nimport re\nfrom pathlib import Path\nimport sys\nimport tempfile\n\nLEGACY={\'readonly.py\':{\'8255c07b8d4046b1c1f4bec310a7af1a2e1386224ad9845127fee9c325038457\',\'4276dd15c15ac88519ece7e1a239eb79873f9977a2ad601b41ed800ae40045eb\',\'474ae35a93a3d807be1f9581957d2b4a6e5aed6928ddb5f536334a4fa4485657\',\'05ee424b11eaabd09ad3db46dee3e183d4b369f14a3b1fcb5da43b839e0c243b\',\'3c1c2b4d2620ba138997734d080403bd175f973f4add6f1430f6af4d0f7182b4\',\'abb388e20fc92eb1f5acabe5f45de4af0e8880f1a6d4bee267fbfc477208d01e\'},\n        \'container_diagnostics.py\':{\'ae4a883cec4a7d061ca08e417ca963812f4499d2a7a3949f1226a5848dfcd6fb\',\'1b2f7540db037f65dba08b8a31ed426fe465de45567785637e55389d5b51e7b5\',\'23633ee9afdd400904ebf11406b8d3b441644b8f17bde48e842b01401166e297\',\'e9ced481f94ef568a2f2116777124869aa5920757256d6936ac5da060c3eb192\',\'ffb9d85621cec4de5ec46c79143545db85be736bcfa0adfb6756f316bf0f8fbe\',\'af82e3af261924f3437ff9b6d049a236e48c63390b6776f9652f075f181fa637\'},\n        \'readonly.sudoers\':{\'2331b5daeb4fcf641320d59fdfb65cfd6e32ffa7df41e81410ed2c1cd3013270\',\'d5e17ce55fecc18b33191de71b001672a49d72edf236bce2e9e738b85ca71c3d\',\'c27aa3af2dc72986f56ae3c5785e715687edaa4b4d7ba2e2ff050c092fac7251\'}}\nDESTINATIONS={\'readonly.py\':\'/usr/local/lib/nexarm-runner/readonly.py\',\n              \'readonly.sudoers\':\'/etc/sudoers.d/nexarm-readonly\',\n              \'container_diagnostics.py\':\'/usr/local/lib/nexarm-runner/container_diagnostics.py\'}\n\n\ndef atomic(path,data,mode):\n    fd,name=tempfile.mkstemp(prefix=\'.nexarm-upgrade-\',dir=path.parent)\n    try:\n        with os.fdopen(fd,\'wb\') as output:\n            output.write(data);output.flush();os.fsync(output.fileno())\n        os.chmod(name,mode);os.replace(name,path)\n    finally:\n        if os.path.exists(name):os.unlink(name)\n\n\ndef require_known_file(name,current,desired):\n    if current!=desired and hashlib.sha256(current).hexdigest() not in LEGACY.get(name,set()):\n        raise ValueError(\'Unknown/custom broker file; preserved\')\n\n\ndef main():\n    if os.geteuid()!=0 or len(sys.argv)!=2:raise SystemExit(\'Operator root upgrade only\')\n    stage=Path(sys.argv[1])\n    enrollment=json.loads(sys.stdin.read().removeprefix("\\ufeff"))\n    if not re.fullmatch(r\'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\', enrollment[\'repository\']):\n        raise ValueError(\'Invalid repository identity\')\n    config_path=Path(\'/etc/nexarm-runner/readonly.json\')\n    if config_path.is_symlink() or config_path.stat().st_uid!=0 or config_path.stat().st_mode&0o077:\n        raise SystemExit(\'Unsafe existing broker config; preserved\')\n    config=json.loads(config_path.read_text(encoding="utf-8-sig"))\n    if config[\'container_id\']!=enrollment[\'container_id\']:raise SystemExit(\'Existing enrolled container differs\')\n    # Validate every file before mutating any; recognize only previous or desired\n    # bytes. A partial prior application is resumable, custom files are preserved.\n    backups=[]\n    for name,destination in DESTINATIONS.items():\n        path=Path(destination);desired=(stage/name).read_bytes()\n        if os.path.lexists(path):\n            info=path.lstat()\n            if path.is_symlink() or info.st_uid!=0 or info.st_mode&0o022:raise SystemExit(\'Unsafe installed broker file\')\n            current=path.read_bytes()\n            require_known_file(name,current,desired)\n            backup=path.with_name(path.name+\'.pre-diagnostics.bak\')\n            if current!=desired:\n                if os.path.lexists(backup):\n                    if backup.is_symlink() or backup.stat().st_uid!=0 or backup.stat().st_mode&0o077:\n                        raise SystemExit(\'Unsafe existing diagnostic backup; preserved\')\n                    require_known_file(name,backup.read_bytes(),desired)\n                else:\n                    backups.append((backup,current))\n        elif name in (\'readonly.py\',\'readonly.sudoers\'):raise SystemExit(\'Expected existing broker/policy missing\')\n    # Grant commands only after validating all enrolled files and config.\n    for backup,current in backups:\n        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)\n        with os.fdopen(fd,\'wb\') as output:output.write(current)\n    config.update(diagnostics_enabled=True,github_repository=enrollment[\'repository\'])\n    atomic(config_path,json.dumps(config,indent=2).encode()+b\'\\n\',0o600)\n    for name in [\'container_diagnostics.py\',\'readonly.py\',\'readonly.sudoers\']:\n        atomic(Path(DESTINATIONS[name]),(stage/name).read_bytes(),0o440 if name.endswith(\'sudoers\') else 0o644)\n    print(\'Enrolled only fixed ROS / Aurora diagnostics; container and registration preserved.\')\n\n\nif __name__==\'__main__\':\n    try:main()\n    except (ValueError,KeyError,OSError):raise SystemExit(\'Diagnostic upgrade refused; check enrollment or host files\') from None\n'}
ROOT = Path(__file__).resolve().parents[1]
FILES = ['deploy/bootstrap-preserve.sh', 'deploy/install-pi-runner-host.sh',
         'deploy/runner/readonly.py', 'deploy/runner/nexarm-readonly',
         'deploy/runner/readonly.sudoers', 'deploy/runner/nexarm-actions.service',
         'deploy/runner/container_diagnostics.py',
         'deploy/runner/upgrade_diagnostics.py']


# GitHub's .NET runner can write .runner as UTF-8 with a BOM. This check
# executes as the dedicated account and only reads the preserved registration.
REGISTRATION_CHECK = 'import json,sys; from pathlib import Path; actual=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig")).get("gitHubUrl","").rstrip("/").lower(); assert actual==sys.argv[2].lower(), "Selected repository differs from preserved runner registration"'


def release_metadata(data):
    version = data['tag_name'].removeprefix('v')
    if not re.fullmatch(r'2\.\d+\.\d+', version):
        raise ValueError('Unexpected runner version')
    name = f'actions-runner-linux-arm64-{version}.tar.gz'
    asset = next(a for a in data['assets'] if a['name'] == name)
    expected_url = f'https://github.com/actions/runner/releases/download/v{version}/{name}'
    if asset['browser_download_url'] != expected_url:
        raise ValueError('Unexpected official artifact URL')
    digest = asset.get('digest', '')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest or ''):
        raise ValueError('Official release lacks SHA256 digest; stop, do not bypass verification')
    return expected_url, digest.split(':')[1]


def verify_archive(path):
    """Resolve archive links with POSIX semantics, including linked ancestors.

    Symlink targets are relative to their parent; hardlink targets are relative
    to the archive root. Check both extraction-time and final graphs so forward
    links and later directory aliases cannot hide an escape.
    """
    with tarfile.open(path) as archive:
        members = archive.getmembers()
    nodes = {}

    def member_name(member):
        name = PurePosixPath(member.name)
        if name.is_absolute() or '..' in name.parts or '\x00' in member.name:
            raise ValueError('Unsafe archive member')
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise ValueError('Unsafe archive member type')
        return tuple(part for part in name.parts if part != '.')

    def resolve(parts, graph, follow_final_symlink=True):
        pending = deque(parts)
        resolved = []
        expansions = 0
        while pending:
            part = pending.popleft()
            if part in ('', '.'):
                continue
            if part == '..':
                if not resolved:
                    raise ValueError('Archive link resolves outside extraction root')
                resolved.pop()
                continue
            resolved.append(part)
            node = graph.get(tuple(resolved))
            if node and node.issym() and not pending and not follow_final_symlink:
                return tuple(resolved)
            if node and (node.issym() or node.islnk()):
                target = PurePosixPath(node.linkname)
                if target.is_absolute() or not node.linkname or '\x00' in node.linkname:
                    raise ValueError('Unsafe archive link')
                expansions += 1
                if expansions > len(graph) + 40:
                    raise ValueError('Cyclic or excessive archive link chain')
                # Hardlinks name an archive-root path, NOT a parent-relative path.
                resolved = resolved[:-1] if node.issym() else []
                pending.extendleft(reversed(target.parts))
            elif node and pending and not node.isdir():
                raise ValueError('Archive path has a non-directory ancestor')
        return tuple(resolved)

    for member in members:
        name = member_name(member)
        if not name and not member.isdir():
            raise ValueError('Unsafe archive root entry')
        parent = resolve(name[:-1], nodes)
        # Track physical destinations after resolving ancestor aliases. Leaving
        # these keyed by their textual archive names can hide a nested link.
        destination = parent + name[-1:]
        for length in range(1, len(parent) + 1):
            prefix = parent[:length]
            if prefix not in nodes:
                directory = tarfile.TarInfo('/'.join(prefix))
                directory.type = tarfile.DIRTYPE
                nodes[prefix] = directory
        previous = nodes.get(destination)
        if previous and not (previous.isdir() and member.isdir()):
            raise ValueError('Duplicate archive member can change link semantics')
        # Validate the destination using links already created by extraction.
        nodes[destination] = member
        if member.issym() or member.islnk():
            resolve(destination, nodes)

    for name, member in nodes.items():
        resolve(name, nodes)
        if member.issym():
            # Validate this header's target even if a later ancestor alias makes
            # its original name unreachable in the final graph.
            parent = resolve(name[:-1], nodes)
            resolve(parent + PurePosixPath(member.linkname).parts, nodes)
        if member.islnk():
            target = resolve(PurePosixPath(member.linkname).parts, nodes, follow_final_symlink=False)
            destination = nodes.get(target)
            if destination is None or not destination.isfile():
                raise ValueError('Archive hardlink must resolve to a regular file')


def assert_runner_idle():
    worker = subprocess.run(['pgrep', '-u', 'nexarm-runner', '-f', 'Runner[.]Worker'])
    if worker.returncode != 1:
        raise ValueError('Runner job is active or cannot be checked; retry when idle')


def runner_has_no_new_privileges():
    pid = int(subprocess.check_output(['systemctl', 'show', '--property=MainPID', '--value', 'nexarm-actions.service'], text=True).strip())
    if pid <= 0:
        raise ValueError('Running service has no main PID; retry after inspecting its status')
    status = Path(f'/proc/{pid}/status').read_text()
    flag = next(line for line in status.splitlines() if line.startswith('NoNewPrivs:'))
    return flag.split()[1] != '0'


def refresh_running_runner(unit_changed):
    """Apply an idle service upgrade; retry a previously deferred restart."""
    if not unit_changed and not runner_has_no_new_privileges():
        return False
    assert_runner_idle()
    run(['sudo', 'systemctl', 'daemon-reload'])
    run(['sudo', 'systemctl', 'restart', 'nexarm-actions.service'])
    if runner_has_no_new_privileges():
        raise ValueError('Live runner still has NoNewPrivs=1; inspect service overrides')
    print('Restarted only the idle runner service; live NoNewPrivs=0 confirmed.')
    return True


LEGACY_RUNNER_UNIT_SHA256 = "6b4ad04a5bf4a75bb6da45377c847518d22006a734751c1fc4d27b58002741a2"


def upgrade_runner_unit(source):
    """Replace only the known historical unit; preserve sudoers/broker/config."""
    unit = '/etc/systemd/system/nexarm-actions.service'
    if subprocess.run(['sudo', 'test', '-L', unit]).returncode == 0:
        raise ValueError('Runner unit is a symlink; preserving it')
    owner, mode = subprocess.check_output(['sudo', 'stat', '-c', '%u %a', unit], text=True).split()
    if owner != '0' or int(mode, 8) & 0o022:
        raise ValueError('Unsafe runner unit ownership/permissions; preserving it')
    existing = subprocess.check_output(['sudo', 'cat', unit])
    dropins = subprocess.check_output(['systemctl', 'show', '--property=DropInPaths', '--value', 'nexarm-actions.service'], text=True).strip()
    if dropins:
        raise ValueError('Runner service has local overrides; preserve and review them first')
    if existing == source.read_bytes():
        return False
    if hashlib.sha256(existing).hexdigest() != LEGACY_RUNNER_UNIT_SHA256:
        raise ValueError('Unknown/custom runner unit; preserving it for review')
    assert_runner_idle()
    run(['sudo', 'systemd-analyze', 'verify', str(source)])
    # Fixed destinations, atomic replacement, one protected backup, no arbitrary
    # files or root-command authorization granted to nexarm-runner.
    code = r'''import hashlib, os, pathlib, tempfile, sys
unit = pathlib.Path("/etc/systemd/system/nexarm-actions.service")
legacy = sys.argv[2]
current = unit.read_bytes()
if unit.is_symlink() or unit.stat().st_uid != 0 or unit.stat().st_mode & 0o022:
    raise SystemExit("Unsafe installed runner unit; preserved")
if hashlib.sha256(current).hexdigest() != legacy:
    raise SystemExit("Unit changed during upgrade; preserved")
backup = unit.with_name(unit.name + ".nexarm-v1.bak")
if os.path.lexists(backup):
    if backup.is_symlink() or backup.stat().st_uid != 0 or backup.stat().st_mode & 0o022 or backup.read_bytes() != current:
        raise SystemExit("Existing backup differs; preserved")
else:
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output: output.write(current)
new = pathlib.Path(sys.argv[1]).read_bytes()
fd, temporary = tempfile.mkstemp(prefix=".nexarm-unit-", dir=unit.parent)
try:
    with os.fdopen(fd, "wb") as output:
        output.write(new); output.flush(); os.fsync(output.fileno())
    os.chmod(temporary, 0o644)
    if unit.read_bytes() != current: raise SystemExit("Unit changed; preserved")
    os.replace(temporary, unit)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
'''
    run(['sudo', '/usr/bin/python3', '-I', '-c', code, str(source), LEGACY_RUNNER_UNIT_SHA256])
    print('Upgraded only the recognized runner unit; old unit backed up. Broker/sudoers/guard unchanged.')
    return True


def ensure_host_setup(stage, container_id, enrollment=None):
    """Reuse identical protected host setup, never reinstall or overwrite it."""
    installed = subprocess.run(['sudo', 'test', '-e', '/usr/local/sbin/nexarm-readonly']).returncode == 0
    if not installed:
        run(['sudo', 'bash', str(stage / 'deploy/install-pi-runner-host.sh')])
        if enrollment is not None:
            run(['sudo','visudo','-cf',str(stage/'deploy/runner/readonly.sudoers')])
            run(['sudo','/usr/bin/python3','-I',str(stage/'deploy/runner/upgrade_diagnostics.py'),str(stage/'deploy/runner')],
                input=json.dumps(dict(enrollment,container_id=container_id)),text=True)
        return False
    groups = subprocess.check_output(['id', '-Gn', 'nexarm-runner'], text=True).split()
    if set(groups) & {'root', 'sudo', 'docker'}:
        raise ValueError('Runner account has unexpected privileged groups')
    guard = Path('/home/pi/.local/share/nexarm/protect-existing').read_text().strip()
    if guard != container_id:
        raise ValueError('Preservation guard differs from live container; review required')
    if enrollment is not None:
        assert_runner_idle()
        run(['sudo','visudo','-cf',str(stage/'deploy/runner/readonly.sudoers')])
        run(['sudo','/usr/bin/python3','-I',str(stage/'deploy/runner/upgrade_diagnostics.py'),str(stage/'deploy/runner')],
            input=json.dumps(dict(enrollment,container_id=container_id)),text=True)
    destinations = {'deploy/runner/readonly.py': '/usr/local/lib/nexarm-runner/readonly.py',
                    'deploy/runner/nexarm-readonly': '/usr/local/sbin/nexarm-readonly',
                    'deploy/runner/readonly.sudoers': '/etc/sudoers.d/nexarm-readonly'}
    for source, target in destinations.items():
        if subprocess.run(['sudo', 'test', '-L', target]).returncode == 0:
            raise ValueError('Installed host file is a symlink; review required')
        result = subprocess.run(['sudo', 'cmp', '-s', str(stage / source), target])
        if result.returncode:
            raise ValueError('Existing host setup differs; preserving it for administrator review')
        owner, mode = subprocess.check_output(['sudo', 'stat', '-c', '%u %a', target], text=True).split()
        if owner != '0' or int(mode, 8) & 0o022:
            raise ValueError('Installed host file has unsafe ownership or permissions')
    # Installed broker also validates its root-owned config and enrolled identity.
    run(['sudo', '-H', '-u', 'nexarm-runner', '/usr/bin/sudo', '-n', '/usr/local/sbin/nexarm-readonly', 'probe'])
    changed = upgrade_runner_unit(stage / 'deploy/runner/nexarm-actions.service')
    print('Resuming: protected broker/account/guard preserved.')
    return changed


def runner_manifest(archive_path):
    """Expected extracted tree from the freshly checksum-verified official tar."""
    manifest = {}
    with tarfile.open(archive_path) as archive:
        for member in archive:
            name = str(PurePosixPath(member.name))
            if name == '.':
                continue
            if member.isfile() or member.islnk():
                with archive.extractfile(member) as contents:
                    digest = hashlib.sha256()
                    while chunk := contents.read(1024 * 1024):
                        digest.update(chunk)
                manifest[name] = ('file', digest.hexdigest())
            elif member.issym():
                manifest[name] = ('link', member.linkname)
            else:
                manifest[name] = ('dir', None)
            for parent in PurePosixPath(name).parents:
                if str(parent) != '.':
                    manifest.setdefault(str(parent), ('dir', None))
    return manifest


def recover_unconfigured_runner(manifest, runner_path='/var/lib/nexarm-runner/actions-runner'):
    """Non-root cleanup of an exact verified package plus failed-config residue."""
    import stat
    root = Path(runner_path)
    if root != Path('/var/lib/nexarm-runner/actions-runner'):
        raise ValueError('Recovery is restricted to the fixed runner extraction directory')
    if root.is_symlink() or root.resolve() != root or not root.is_dir() or os.path.ismount(root):
        raise ValueError('Unsafe runner directory; nothing removed')
    uid = os.geteuid()
    if uid == 0 or root.stat().st_uid != uid:
        raise ValueError('Recovery must run as the dedicated runner directory owner')
    # A configured runner is protected regardless of marker contents/type.
    def protected():
        return any(p.name.startswith(('.runner', '.credentials')) or p.name == '.service'
                   for p in root.iterdir())
    if protected():
        raise ValueError('Configured/credentialed runner found; nothing removed')
    if not {'.env', '.path', '_diag'} <= {p.name for p in root.iterdir()}:
        raise ValueError('Not the recognized failed-registration residue; nothing removed')
    # Detect bind mounts too: os.path.ismount alone misses same-filesystem binds.
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        mount = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), line.split()[4])
        if mount == str(root) or mount.startswith(str(root) + '/'):
            raise ValueError('Runner extraction contains a mount; nothing removed')
    # Service status is checked by the caller; refuse any other runner-user process
    # too, including an unfinished config.sh or independently started listener.
    for process in Path('/proc').iterdir():
        if process.name.isdigit() and int(process.name) != os.getpid():
            try:
                if process.stat().st_uid == uid:
                    raise ValueError('Runner account has another process; nothing removed')
            except FileNotFoundError:
                pass
    allowed = {'.env', '.path', '_diag'}
    for item in root.rglob('*'):
        relative = item.relative_to(root).as_posix()
        info = item.lstat()
        if info.st_uid != uid or os.path.ismount(item):
            raise ValueError('Unexpected ownership/mount in runner residue')
        expected = manifest.get(relative)
        if expected:
            kind, value = expected
            if kind == 'link':
                if not stat.S_ISLNK(info.st_mode) or os.readlink(item) != value:
                    raise ValueError('Runner package link differs; nothing removed')
            elif kind == 'dir':
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError('Runner package directory differs; nothing removed')
            else:
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Runner package file differs; nothing removed')
                digest = hashlib.sha256()
                with item.open('rb') as contents:
                    while chunk := contents.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != value:
                    raise ValueError('Runner package contents differ; nothing removed')
        elif relative in allowed:
            if not (stat.S_ISDIR(info.st_mode) if relative == '_diag' else stat.S_ISREG(info.st_mode)):
                raise ValueError('Unsafe failed-registration residue')
        elif relative.startswith('_diag/'):
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError('Unsafe diagnostic residue')
        else:
            raise ValueError('Unknown runner-directory contents; nothing removed')
    for relative in manifest:
        if not os.path.lexists(root / relative):
            raise ValueError('Runner package is incomplete; nothing removed')
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError('Platform lacks safe directory removal; nothing removed')
    parent_fd = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if protected():
            raise ValueError('Registration appeared during inspection; nothing removed')
        shutil.rmtree(root.name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    print('Removed only verified unconfigured runner extraction and failed-registration residue.')


def recover_runner_residue(archive, runner):
    """Pass only public package hashes to cleanup running as nexarm-runner."""
    import inspect
    state = subprocess.check_output(['systemctl', 'show', '--property=ActiveState', '--value', 'nexarm-actions.service'], text=True).strip()
    if state not in ('inactive', 'failed'):
        raise ValueError('Runner service is not stopped; residue preserved')
    manifest = runner_manifest(archive)
    # The helper cannot access the pi-owned staging directory. Supply code plus
    # hashes over stdin, not root privileges or a mutable installed host helper.
    code = 'import os, json, hashlib, shutil, re\nfrom pathlib import Path\n' + inspect.getsource(recover_unconfigured_runner)
    code += '\nrecover_unconfigured_runner(' + repr(manifest) + ')\n'
    run(['sudo', '-H', '-u', 'nexarm-runner', '/usr/bin/python3', '-I', '-'], input=code, text=True)


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def main():
    if getpass.getuser() != 'pi' or os.geteuid() == 0 or platform.machine() != 'aarch64':
        raise SystemExit('Run as pi (not sudo) on 64-bit Raspberry Pi Linux')
    if os.environ.get('HOME') != '/home/pi':
        raise SystemExit('Expected HOME=/home/pi')
    for command in ['sudo', 'docker', 'systemctl', 'systemd-analyze', 'visudo', 'useradd', 'tar', 'pgrep']:
        if shutil.which(command, path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin") is None:
            raise SystemExit(f'Missing prerequisite: {command}; nothing installed automatically')
    run(['sudo', '-v'])
    repository = input('GitHub repository [minciunageorge/nexarm]: ').strip() or 'minciunageorge/nexarm'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise SystemExit('Use owner/repository, not a URL')
    baseline = subprocess.check_output(['docker', 'inspect', '-f', '{{.Id}} {{.State.Running}} {{.State.StartedAt}}', 'nexarm'], text=True).strip()
    if not re.fullmatch(r'[0-9a-f]{64} true .+', baseline):
        raise SystemExit('Expected running nexarm; no container changes attempted')
    files = PAYLOAD or {name: (ROOT / name).read_text() for name in FILES}
    with tempfile.TemporaryDirectory(prefix='nexarm-activation-') as temporary:
        stage = Path(temporary)
        for name, contents in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents)
        registered='/var/lib/nexarm-runner/actions-runner/.runner'
        if subprocess.run(['sudo','test','-f',registered]).returncode==0:
            run(['sudo','-H','-u','nexarm-runner','/usr/bin/python3','-I','-c',REGISTRATION_CHECK,registered,f'https://github.com/{repository}'])
        unit_changed = ensure_host_setup(stage, baseline.split()[0],
                                         {'repository':repository})
        result = subprocess.run(['systemctl', 'is-active', '--quiet', 'nexarm-actions.service'])
        if result.returncode == 0:
            if not refresh_running_runner(unit_changed):
                print('Runner service already active; no restart or re-registration performed.')
        else:
            runner = '/var/lib/nexarm-runner/actions-runner'
            configured = subprocess.run(['sudo', 'test', '-f', runner+'/.runner']).returncode == 0
            if not configured:
                # No token needed for this public official-release metadata request.
                request = urllib.request.Request('https://api.github.com/repos/actions/runner/releases/latest',
                                                 headers={'Accept':'application/vnd.github+json','User-Agent':'nexarm-bootstrap'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    url, digest = release_metadata(json.load(response))
                archive = stage / 'runner.tar.gz'
                sha = hashlib.sha256()
                with urllib.request.urlopen(url, timeout=60) as response, archive.open('wb') as output:
                    while chunk := response.read(1024*1024):
                        sha.update(chunk); output.write(chunk)
                if sha.hexdigest() != digest:
                    raise SystemExit('SHA256 mismatch: runner will not be extracted or used')
                verify_archive(archive)
                if subprocess.run(['sudo', 'test', '-e', runner]).returncode == 0:
                    recover_runner_residue(archive, runner)
                run(['sudo', 'install', '-d', '-o', 'nexarm-runner', '-g', 'nexarm-runner', '-m', '700', runner])
                # Runner package is extracted as its own user, never as root.
                run(['sudo', 'install', '-o', 'nexarm-runner', '-g', 'nexarm-runner', '-m', '600', str(archive), runner+'/package.tar.gz'])
                run(['sudo', '-H', '-u', 'nexarm-runner', 'tar', '-xzf', runner+'/package.tar.gz', '-C', runner])
                run(['sudo', '-u', 'nexarm-runner', 'rm', runner+'/package.tar.gz'])
                print(f'In GitHub: {repository} → Settings → Actions → Runners → New self-hosted runner; select Linux ARM64.')
                token = getpass.getpass('Paste the SHORT-LIVED registration token here (hidden, never in chat): ')
                if not token.strip():
                    raise SystemExit('No registration token supplied; service stays stopped')
                # Token goes over stdin and into the runner-supported input environment,
                # not argv/history. sudo starts a shell with a fixed script only.
                registration = 'set -eu; cd /var/lib/nexarm-runner/actions-runner; IFS= read -r token; export ACTIONS_RUNNER_INPUT_TOKEN="$token"; unset token; exec ./config.sh --unattended --url "$1" --name nexarm-pi5-readonly --labels nexarm-readonly --work _work'
                run(['sudo', '-H', '-u', 'nexarm-runner', 'bash', '-c', registration, 'register', f'https://github.com/{repository}'],
                    input=token+'\n', text=True)
                token = None
            run(['sudo', 'systemd-analyze', 'verify', '/etc/systemd/system/nexarm-actions.service'])
            run(['sudo', 'systemctl', 'daemon-reload'])
            run(['sudo', 'systemctl', 'enable', '--now', 'nexarm-actions.service'])
        after = subprocess.check_output(['docker', 'inspect', '-f', '{{.Id}} {{.State.Running}} {{.State.StartedAt}}', 'nexarm'], text=True).strip()
        if after != baseline:
            raise SystemExit('Container state differs from baseline; investigate external changes. Bootstrap never modifies it.')
        print('Existing nexarm ID, running state and start time unchanged.')
        print('Activation requested. Confirm runner online in GitHub, then dispatch Pi harmless verification on main.')
        print('Service PID/active status alone is not a passed GitHub job. No deployment or motion enabled.')


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as error:
        raise SystemExit(f'Official GitHub download request failed (HTTP {error.code}); check Pi outbound access. Container untouched.') from None
    except ValueError as error:
        raise SystemExit(f'Activation stopped: {error}. No container modification attempted.') from None
    except subprocess.CalledProcessError as error:
        raise SystemExit(f'Activation step {Path(error.cmd[0]).name} failed (exit {error.returncode}); inspect its output. No container modification attempted.') from None
    except (KeyError, StopIteration, OSError, subprocess.SubprocessError, tarfile.TarError):
        # Never echo exception details that could include registration credentials.
        raise SystemExit('Activation stopped safely. Check connectivity/prerequisites or existing partial setup; container was not modified.') from None
