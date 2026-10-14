"""Prepared camera-only transaction engine. NOT exposed to the Pi runner.

Live enrollment/invocation is deliberately unavailable until the actual driver,
launcher and SDK sample have been reviewed. Importing this module performs no IO.
"""
import hashlib
import fcntl
import json
import tempfile
import os
import resource
from pathlib import Path
import signal
import subprocess
import time


REQUIRED_REVIEWS = ('dedicated_camera_process', 'launcher_no_respawn',
                    'launcher_exit_affects_camera_only', 'stable_parameter_files',
                    'sdk_no_device_reset_or_configuration', 'sdk_does_not_fork',
                    'sdk_private_workdir_supported')


def validate_plan(plan):
    if plan.get('enabled') is not True:
        raise ValueError('Exclusive SDK test is unarmed')
    if any(plan.get('review', {}).get(key) is not True for key in REQUIRED_REVIEWS):
        raise ValueError('Camera isolation / launcher / SDK review is incomplete')
    for role, basename in [('driver', 'aurora930_node'), ('sdk', 'SimpleView_FetchFrame')]:
        entry = plan[role]
        path = Path(entry['path'])
        if not path.is_absolute() or path.name != basename or '..' in path.parts:
            raise ValueError('Only the two exact enrolled executables are permitted')
        digest = entry['sha256']
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Executable SHA256 required')
    if type(plan['driver']['pid']) is not int or plan['driver']['pid'] <= 1:
        raise ValueError('Never target PID 1 or a process group')
    if type(plan['driver']['start_ticks']) is not int:
        raise ValueError('Driver process generation must be pinned')
    if type(plan.get('sdk_timeout_seconds')) is not int or not 1 <= plan['sdk_timeout_seconds'] <= 15:
        raise ValueError('SDK timeout must be 1..15 seconds')
    if not plan.get('pid_namespace'):
        raise ValueError('Container PID namespace enrollment required')


def transaction(plan, backend):
    """Guard first; once stop may have occurred, always attempt exact restoration."""
    validate_plan(plan)
    snapshot = backend.preflight(plan)  # MUST have no mutating side effects.
    report = {'sdk_executed': False, 'driver_restored': False, 'status': 'failed'}
    stopping = False
    try:
        stopping = True  # stop can partially succeed before raising.
        backend.stop_driver(snapshot)
        backend.confirm_exclusive(snapshot)
        report['sdk_executed'] = True
        report['sdk'] = backend.run_sdk(plan['sdk_timeout_seconds'], snapshot)
        report['status'] = 'completed' if report['sdk']['exit_code'] == 0 else 'sdk_failed'
    except Exception as error:
        # Never export arguments, environment, parameter contents or raw errors.
        report['failure_type'] = type(error).__name__
    finally:
        if stopping:
            try:
                backend.restore_driver(snapshot)
                report['driver_restored'] = True
            except Exception as error:
                report['status'] = 'RESTORE_FAILED_OPERATOR_REQUIRED'
                report['restore_failure_type'] = type(error).__name__
    return report


class PinnedProcessBackend:
    """Future in-container backend; never accepts a caller command/PID/SDK flag."""
    def __init__(self):
        self.sdk = None

    @staticmethod
    def generation(pid):
        return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19])

    @staticmethod
    def verify_file(entry):
        path = Path(entry['path'])
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Enrolled binary must be regular, root-owned and protected')
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024*1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != entry['sha256']:
            raise ValueError('Enrolled binary changed')

    def preflight(self, plan):
        # This backend only supports the existing root-run camera process. A UID
        # change or unsupported launcher model refuses BEFORE sending any signal.
        if os.geteuid() != 0 or os.readlink('/proc/self/ns/pid') != plan['pid_namespace']:
            raise ValueError('Wrong execution identity or container namespace')
        for role in ('driver', 'sdk'):
            self.verify_file(plan[role])
        pid = plan['driver']['pid']
        handle = os.pidfd_open(pid)
        try:
            if self.generation(pid) != plan['driver']['start_ticks']:
                raise ValueError('Driver PID generation changed')
            if os.readlink(f'/proc/{pid}/exe') != plan['driver']['path']:
                raise ValueError('Target is not the pinned camera executable')
            fields = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
            own=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
            for key in ('Gid','Groups','Umask','CapEff','CapPrm','CapBnd','NoNewPrivs','Seccomp'):
                if fields.get(key,'').strip()!=own.get(key,'').strip():
                    raise ValueError('Cannot reproduce original process security context')
            if fields['Uid'].split()[0] != '0':
                raise ValueError('Original driver UID is unsupported; never change its UID')
            argv = Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
            argv = [os.fsdecode(value) for value in argv]
            if argv != plan['driver']['argv'] or argv[0] != plan['driver']['path']:
                raise ValueError('Original driver arguments changed')
            cwd = os.readlink(f'/proc/{pid}/cwd')
            if cwd != plan['driver']['cwd']:
                raise ValueError('Original working directory changed')
            env_data = Path(f'/proc/{pid}/environ').read_bytes()
            if len(env_data) > 128*1024:
                raise ValueError('Driver environment size limit exceeded')
            env = dict(os.fsdecode(item).split('=', 1) for item in env_data.rstrip(b'\0').split(b'\0'))
            if not env or 'LD_PRELOAD' in env or 'LD_AUDIT' in env:
                raise ValueError('Unreviewed preload environment')
            # Params must be stable/protected rather than ephemeral launch files.
            for index, value in enumerate(argv[:-1]):
                if value == '--params-file':
                    path = Path(argv[index+1]);info = path.lstat()
                    if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022 or not path.is_file():
                        raise ValueError('Unsafe original camera parameter file')
                    if str(path).startswith('/tmp/'):
                        raise ValueError('Ephemeral launch parameters require stable enrollment first')
            return {'pid':pid,'handle':handle,'argv':argv,'cwd':cwd,'env':env,'plan':plan,'restored':None}
        except BaseException:
            os.close(handle)
            raise

    def stop_driver(self, snapshot):
        signal.pidfd_send_signal(snapshot['handle'], signal.SIGTERM)
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            try:
                if self.generation(snapshot['pid']) != snapshot['plan']['driver']['start_ticks']:
                    return
                status = Path(f"/proc/{snapshot['pid']}/stat").read_text().rsplit(')',1)[1].split()[0]
                if status == 'Z':return
            except FileNotFoundError:return
            time.sleep(0.05)
        # No escalation to SIGKILL on the existing driver; fail and restore/check.
        raise TimeoutError('Camera did not exit after SIGTERM')

    def confirm_exclusive(self, snapshot):
        target = snapshot['plan']['driver']['path']
        for entry in Path('/proc').iterdir():
            if not entry.name.isdecimal():continue
            try:
                if os.readlink(entry/'exe') == target:
                    raise RuntimeError('Camera still alive or respawned; SDK will not be run')
            except FileNotFoundError:continue
            except PermissionError:raise RuntimeError('Cannot prove camera-process exclusivity') from None

    @staticmethod
    def reset_child_signals():
        signal.signal(signal.SIGTERM,signal.SIG_DFL)
        signal.signal(signal.SIGINT,signal.SIG_DFL)

    @staticmethod
    def sdk_child_setup():
        PinnedProcessBackend.reset_child_signals()
        resource.setrlimit(resource.RLIMIT_FSIZE,(8*1024*1024,8*1024*1024))

    def run_sdk(self, timeout, snapshot):
        self.verify_file(snapshot['plan']['sdk'])
        directory=Path(tempfile.mkdtemp(prefix='sdk-output-',dir='/tmp/nexarm-exclusive-sdk'))
        # SDK output cannot overwrite the driver's original working directory.
        fd=os.open(directory/'sdk.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as output:
            self.sdk=subprocess.Popen([snapshot['plan']['sdk']['path']],
                cwd=directory,env=snapshot['env'],stdin=subprocess.DEVNULL,
                stdout=output,stderr=output,start_new_session=True,
                preexec_fn=self.sdk_child_setup)
            reason=None;deadline=time.monotonic()+timeout
            try:
                while self.sdk.poll() is None:
                    if time.monotonic()>=deadline:
                        reason='timeout';break
                    paths=list(directory.iterdir())
                    if len(paths)>128 or sum(path.lstat().st_size for path in paths)>64*1024*1024:
                        reason='output_limit';break
                    time.sleep(0.05)
                if reason:
                    self.sdk.terminate()
                    try:self.sdk.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.sdk.kill();self.sdk.wait(timeout=2)
                return {'exit_code':self.sdk.returncode,'timed_out':reason=='timeout',
                        'stop_reason':reason,'private_output_directory':str(directory)}
            finally:
                if self.sdk.poll() is None:
                    self.sdk.kill();self.sdk.wait(timeout=2)

    def restore_driver(self, snapshot):
        try:
            if self.sdk is not None and self.sdk.poll() is None:
                raise RuntimeError('SDK still alive; do not start a competing camera driver')
            # A refused stop/respawn must not create a duplicate driver.
            for entry in Path('/proc').iterdir():
                if not entry.name.isdecimal():continue
                try:
                    if os.readlink(entry/'exe') == snapshot['plan']['driver']['path']:
                        if entry.name == str(snapshot['pid']) and self.generation(snapshot['pid']) == snapshot['plan']['driver']['start_ticks']:
                            return  # Original driver never exited.
                        raise RuntimeError('Unexpected respawn; manual reconciliation required')
                except FileNotFoundError:continue
            self.verify_file(snapshot['plan']['driver'])
            snapshot['restored'] = subprocess.Popen(snapshot['argv'],cwd=snapshot['cwd'],env=snapshot['env'],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,preexec_fn=self.reset_child_signals)
            time.sleep(1)
            if snapshot['restored'].poll() is not None:
                raise RuntimeError('Restored driver exited; report operator recovery required')
        finally:os.close(snapshot['handle'])


def write_report(report):
    """Private persistent state; never contain original argv/env/parameters."""
    root=Path('/tmp/nexarm-exclusive-sdk')
    if root.is_symlink():raise ValueError('Unsafe journal directory')
    root.mkdir(mode=0o700,exist_ok=True)
    info=root.stat()
    if info.st_uid!=0 or info.st_mode&0o077:raise ValueError('Unsafe journal ownership/mode')
    fd,name=tempfile.mkstemp(prefix='.report-',dir=root)
    try:
        with os.fdopen(fd,'w') as output:
            json.dump(report,output);output.flush();os.fsync(output.fileno())
        os.chmod(name,0o600);os.replace(name,root/'report.json')
    finally:
        if os.path.exists(name):os.unlink(name)


def run_guardian(plan, backend_factory):
    """Independent session owns stop/test/restore if the caller disappears.

    No guardian can guarantee recovery after its own SIGKILL, a host reboot or
    hardware failure. Persistent state makes unresolved restoration explicit.
    """
    validate_plan(plan)
    pid=os.fork()
    if pid==0:
        os.setsid()
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        signal.signal(signal.SIGINT,signal.SIG_IGN)
        lock=None
        try:
            root=Path('/tmp/nexarm-exclusive-sdk')
            if root.is_symlink():raise ValueError('Unsafe journal directory')
            root.mkdir(mode=0o700,exist_ok=True)
            info=root.stat()
            if info.st_uid!=0 or info.st_mode&0o077:raise ValueError('Unsafe journal ownership/mode')
            lock=os.open('/tmp/nexarm-exclusive-sdk/lock',os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
            lock_info=os.fstat(lock)
            if lock_info.st_uid!=0 or lock_info.st_mode&0o077:raise ValueError('Unsafe existing lock file')
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if (root/'report.json').exists():
                previous=json.loads((root/'report.json').read_text())
                if previous.get('driver_restored') is not True:
                    raise ValueError('Prior restoration unresolved; refuse another transaction')
            write_report({'status':'transaction_started','driver_restored':False})
            report=transaction(plan,backend_factory())
            write_report(report)
            os._exit(0 if report['driver_restored'] else 2)
        except BaseException:os._exit(2)
        finally:
            if lock is not None:os.close(lock)
    return pid


if __name__ == '__main__':
    raise SystemExit('Prepared only: live SDK test is not enrolled or exposed to the runner')
