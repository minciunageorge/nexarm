#!/usr/bin/python3
"""Root-installed, fixed-scope Docker read broker. Never import checkout code."""
import json
import base64
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import tarfile
import tempfile

CONFIG = Path('/etc/nexarm-runner/readonly.json')
DOCKER = '/usr/bin/docker'
OPERATIONS = ('probe', 'discover-existing', 'collect-existing', 'ros-inspect', 'camera-one-frame', 'aurora-health', 'aurora-discover', 'aurora-recover')


def validate(config):
    identifier = config['container_id']
    if not re.fullmatch(r'[0-9a-f]{64}', identifier):
        raise ValueError('Invalid enrolled container ID')
    paths = config.get('artifact_paths', [])
    if not isinstance(paths, list) or len(paths) > 8:
        raise ValueError('Invalid artifact path list')
    for path in paths:
        if not isinstance(path, str) or not path.startswith('/') or path == '/' or '..' in Path(path).parts or '\n' in path:
            raise ValueError('Invalid artifact path')
        if Path(path).parts[1] in ('dev', 'proc', 'sys', 'etc'):
            raise ValueError('System paths are not result directories')
    return identifier, paths


def docker(arguments, output=None):
    command = [DOCKER, '--host', 'unix:///var/run/docker.sock', *arguments]
    result = subprocess.run(command, stdout=output or subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=10 if arguments[0]=='inspect' else 120, env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8'})
    if result.returncode:
        raise RuntimeError('Docker read operation failed; confirm container and enrolled paths')
    return result.stdout


def host_usb_inventory():
    """Fixed sysfs attributes only; never open USB, reset, unbind or read serials."""
    root=Path('/sys/bus/usb/devices')
    if not root.exists():return {'visible':False,'devices':[]}
    entries=sorted(root.iterdir(),key=lambda path:path.name)
    if len(entries)>256:raise ValueError('Host USB inventory limit exceeded')
    devices=[]
    for path in entries:
        try:
            vendor=(path/'idVendor').read_text()[:16].strip()
            product=(path/'idProduct').read_text()[:16].strip()
        except OSError:continue
        if not re.fullmatch('[0-9a-fA-F]{4}',vendor) or not re.fullmatch('[0-9a-fA-F]{4}',product):continue
        item={'sysfs_name':path.name,'vendor_id':vendor,'product_id':product}
        for key in ('manufacturer','product','authorized','busnum','devnum','speed','bConfigurationValue'):
            try:
                with (path/key).open() as source:item[key]=source.read(128).strip()
            except OSError:pass
        interfaces=[]
        for interface in entries:
            if interface.name.startswith(path.name+':'):
                try:driver=Path(os.readlink(interface/'driver')).name
                except OSError:driver=None
                interfaces.append({'interface':interface.name,'driver':driver})
        item['interfaces']=interfaces
        devices.append(item)
    return {'visible':True,'devices':devices,'scope':'Presence/authorization/interface binding only; no transfer or reset'}


def timeout_diagnostics(data):
    """Copy only bounded fixed-topic numeric/QoS data from the observer."""
    result=json.loads(data)['camera_timeout']
    if not isinstance(result,dict): raise ValueError('Invalid timeout diagnostics')
    if result.get('topic')!='/depth_cam/rgb/image_raw': raise ValueError('Unexpected diagnostic topic')
    count=result['publisher_count']
    if type(count) is not int or not 0<=count<=200: raise ValueError('Invalid publisher count')
    clean={'topic':result['topic'],'publisher_count':count}
    for field,allowed in [('offered_reliability',{'RELIABLE','BEST_EFFORT','SYSTEM_DEFAULT','UNKNOWN'}),
                          ('offered_durability',{'VOLATILE','TRANSIENT_LOCAL','SYSTEM_DEFAULT','UNKNOWN'})]:
        values=result[field]
        if not isinstance(values,list) or len(values)>200 or any(type(v) is not str or v not in allowed for v in values):
            raise ValueError('Invalid offered QoS')
        clean[field]=values
    attempts=result['attempts']
    if not isinstance(attempts,list) or not 1<=len(attempts)<=2: raise ValueError('Invalid attempt count')
    clean['attempts']=[]
    for index,attempt in enumerate(attempts):
        if not isinstance(attempt,dict): raise ValueError('Invalid delivery attempt')
        reliability=('RELIABLE','BEST_EFFORT')[index]
        if attempt.get('reliability')!=reliability or attempt.get('durability')!='VOLATILE':
            raise ValueError('Invalid requested QoS')
        item={'reliability':reliability,'durability':'VOLATILE'}
        for field in ('matched_publishers','max_matched_publishers','messages_received'):
            value=attempt[field]
            if type(value) is not int or not 0<=value<=200: raise ValueError('Invalid delivery count')
            item[field]=value
        clean['attempts'].append(item)
    return json.dumps(clean)


def discovery_failure(operation, errors, exit_code=None, timed_out=False):
    """Bounded real stderr/exit cause for the fixed discovery/report operations."""
    errors.seek(0,2);size=errors.tell();errors.seek(0)
    if size>65536:
        first=errors.read(16384);errors.seek(-49152,2)
        reason=first+b'\n[stderr truncated: middle omitted]\n'+errors.read(49152)
    else:reason=errors.read(65536)
    signal_number = -exit_code if exit_code is not None and exit_code<0 else (exit_code-128 if exit_code is not None and 128<exit_code<=192 else None)
    return json.dumps({'operation':operation, 'status':'failed', 'exit_code':exit_code,
                       'possible_signal':signal_number, 'docker_client_timed_out':timed_out,
                       'timeout_seconds':25 if timed_out else None,
                       'stderr_bytes':size,'stderr_truncated':size>65536,
                       'stderr':reason.decode('utf-8',errors='replace')})


def observe(operation, identifier):
    # No caller-controlled command/topic/UID/env. No helper copied into container.
    script=Path(__file__).with_name('container_diagnostics.py').read_bytes()
    command=[DOCKER,'--host','unix:///var/run/docker.sock','exec','-i','--user','65534:65534',
             '--env','HOME=/tmp','--env','ROS_LOG_DIR=/tmp',
             '--env','LD_PRELOAD=','--env','LD_LIBRARY_PATH=/opt/ros/humble/lib',
             '--env','AMENT_PREFIX_PATH=/opt/ros/humble','--env','RCUTILS_LOGGING_USE_STDOUT=0',
             identifier,'/usr/bin/python3','-I','-',operation]
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            completed=subprocess.run(command,input=script,stdout=output,stderr=errors,timeout=25,check=False,
                           env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','LANG':'C.UTF-8'},
                           preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE,(4*1024*1024,4*1024*1024)))
        except subprocess.TimeoutExpired:
            if operation in ('aurora-discover','aurora-recover'):
                raise ValueError(discovery_failure(operation,errors,timed_out=True)) from None
            raise
        if completed.returncode:
            if operation in ('aurora-discover','aurora-recover'):
                raise ValueError(discovery_failure(operation,errors,exit_code=completed.returncode))
            errors.seek(0); reason=errors.read(4096)
            details=None
            if operation=='camera-one-frame':
                output.seek(0)
                try:
                    details=timeout_diagnostics(output.read(64*1024+1))
                except (ValueError,KeyError,TypeError):
                    pass
            if details is not None or b'TimeoutError: No Aurora RGB frame' in reason:
                raise ValueError('No Aurora RGB frame within 15 seconds; '+
                                 (details or 'No valid bounded delivery diagnostics returned'))
            if b'Expected a 640x400' in reason:
                raise ValueError('Existing camera frame has unsupported dimensions or encoding')
            if b'ModuleNotFoundError' in reason or b'ImportError' in reason:
                raise ValueError('Existing container lacks compatible Humble Python modules; no packages were installed')
            raise ValueError('Fixed ROS observer failed; check Humble Python 3.10, existing publisher and container DDS access')
        output.seek(0); data=output.read(4*1024*1024+1)
    if len(data)>4*1024*1024: raise ValueError('Observer output limit exceeded')
    result=json.loads(data)
    if operation in ('aurora-discover','aurora-recover'):
        if result.get('operation')!=operation or result.get('recovery',{}).get('enabled') is not False or result.get('recovery',{}).get('status')!='refused':
            raise ValueError('Discovery cannot enable camera recovery')
        try:
            result['host_usb']=host_usb_inventory()
        except (OSError,ValueError) as error:
            result['host_usb']={'visible':None,'devices':[],'error_type':type(error).__name__,'error':str(error)[:4096]}
            result['status']='partial'
        print(json.dumps(result))
    elif operation=='aurora-health':
        expected={'rgb':'/depth_cam/rgb/image_raw','depth':'/depth_cam/depth/image_raw','ir':'/depth_cam/ir/image_raw'}
        streams=result.get('streams')
        if result.get('operation')!='aurora-health' or not isinstance(streams,dict) or set(streams)!=set(expected):
            raise ValueError('Malformed Aurora health result')
        if any(not isinstance(streams[name],dict) or streams[name].get('topic')!=topic for name,topic in expected.items()):
            raise ValueError('Unexpected Aurora health topic')
        print(json.dumps(result))
    elif operation=='ros-inspect':
        if not isinstance(result.get('nodes'),list) or not isinstance(result.get('topics'),list):
            raise ValueError('Malformed ROS graph result')
        if len(result['nodes'])>200 or len(result['topics'])>200: raise ValueError('Graph limit exceeded')
        print(json.dumps(result))
    else:
        if result.get('topic')!='/depth_cam/rgb/image_raw' or (result.get('width'),result.get('height'))!=(640,400) or result.get('encoding')!='rgb8':
            raise ValueError('Unexpected camera result')
        pixels=base64.b64decode(result.pop('pixels'),validate=True)
        if len(pixels)!=640*400*3: raise ValueError('Unexpected frame length')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'aurora-rgb.ppm').write_bytes(b'P6\n640 400\n255\n'+pixels)
            (root/'camera.json').write_text(json.dumps(result,indent=2)+'\n')
            with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
                for path in root.iterdir(): archive.add(path,arcname=path.name)


def execute(operation, config):
    if operation not in OPERATIONS:
        raise ValueError('Only read-only operations are enabled')
    if operation in ('ros-inspect', 'camera-one-frame', 'aurora-health', 'aurora-discover', 'aurora-recover') and config.get('diagnostics_enabled') is not True:
        raise ValueError('ROS/camera diagnostics are not enrolled')
    identifier, paths = validate(config)
    current = docker(['inspect', '-f', '{{.Id}}', 'nexarm']).decode().strip()
    if current != identifier:
        raise ValueError('Container identity changed; administrator review required')
    if operation in ('ros-inspect','camera-one-frame','aurora-health','aurora-discover','aurora-recover'):
        before=docker(['inspect','-f','{{.Id}} {{.State.Running}} {{.State.StartedAt}}',identifier])
        observe(operation,identifier)
        if before!=docker(['inspect','-f','{{.Id}} {{.State.Running}} {{.State.StartedAt}}',identifier]):
            raise ValueError('Container state changed during observation')
    elif operation == 'probe':
        sys.stdout.buffer.write(docker(['inspect', '-f', 'id={{.Id}} running={{.State.Running}} image={{.Image}} started={{.State.StartedAt}}', identifier]))
    elif operation == 'discover-existing':
        template = 'user={{.Config.User}} workdir={{.Config.WorkingDir}}{{println}}{{range .Config.Env}}{{if ge (len .) 12}}{{if eq (slice . 0 12) "ROS_LOG_DIR="}}{{println .}}{{end}}{{end}}{{if ge (len .) 5}}{{if eq (slice . 0 5) "HOME="}}{{println .}}{{end}}{{end}}{{end}}'
        sys.stdout.buffer.write(docker(['inspect', '-f', template, identifier]))
        sys.stdout.buffer.write(docker(['inspect', '-f', '{{range .Mounts}}{{println .Type .Source .Destination}}{{end}}', identifier]))
    else:
        with tempfile.TemporaryDirectory(prefix='nexarm-readonly-') as temporary:
            root = Path(temporary)
            (root / 'status.txt').write_bytes(docker(['inspect', '-f', 'id={{.Id}} running={{.State.Running}} image={{.Image}} started={{.State.StartedAt}}', identifier]))
            command = [DOCKER, '--host', 'unix:///var/run/docker.sock', 'logs', '--timestamps', '--since', '24h', '--tail', '5000', identifier]
            with (root / 'container.log').open('wb') as logs:
                subprocess.run(command, stdout=logs, stderr=subprocess.STDOUT, timeout=120, check=True,
                               env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8'})
            for index, path in enumerate(paths, 1):
                docker(['cp', f'{identifier}:{path}', str(root / f'path-{index}')])
            (root / 'paths.json').write_text(json.dumps(paths, indent=2)+'\n')
            total = 0
            for path in root.rglob('*'):
                if path.is_symlink() or not (path.is_file() or path.is_dir()):
                    raise ValueError('Special files/symlinks not permitted in enrolled artifacts')
                if path.is_file():
                    total += path.stat().st_size
            if total > 64 * 1024 * 1024:
                raise ValueError('Collection exceeds 64 MiB; narrow enrolled result paths')
            with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
                for path in sorted(root.iterdir()):
                    archive.add(path, arcname=path.name, recursive=True)


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in OPERATIONS:
        raise SystemExit('Only fixed approved diagnostic operations are enabled')
    # Installed config is root-owned; never honor caller environment/config paths.
    info = CONFIG.stat()
    if info.st_uid != 0 or info.st_mode & 0o022 or CONFIG.is_symlink():
        raise SystemExit('Unsafe installed configuration ownership/permissions')
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
        execute(sys.argv[1], json.loads(CONFIG.read_text()))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except (KeyError, RuntimeError, OSError, subprocess.SubprocessError):
        raise SystemExit('Broker failed closed: verify enrollment, container identity and observer timeout') from None


if __name__ == '__main__':
    main()
