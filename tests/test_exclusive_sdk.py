"""Transaction tests use a fake backend: no SDK execution or process signals."""
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('exclusive_sdk',ROOT/'deploy/runner/exclusive_sdk.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def plan():
    return {'enabled':True,'review':{key:True for key in module.REQUIRED_REVIEWS},
            'driver':{'path':'/opt/vendor/aurora930_node','sha256':'a'*64,'pid':7,'start_ticks':123},
            'sdk':{'path':'/opt/vendor/SimpleView_FetchFrame','sha256':'b'*64},
            'sdk_timeout_seconds':10,'pid_namespace':'pid:[test]'}

class Backend:
    def __init__(self,fail=None):self.calls=[];self.fail=fail
    def step(self,name):
        self.calls.append(name)
        if self.fail==name:raise RuntimeError(name)
    def preflight(self,plan):self.step('preflight');return {}
    def stop_driver(self,snapshot):self.step('stop')
    def confirm_exclusive(self,snapshot):self.step('exclusive')
    def run_sdk(self,timeout,snapshot):self.step('sdk');return {'exit_code':0,'timed_out':False}
    def restore_driver(self,snapshot):self.step('restore')

class ExclusiveTests(unittest.TestCase):
    def test_unarmed_unreviewed_wrong_binary_and_pid_one_never_stop(self):
        for field,value in [('enabled',False),('sdk_timeout_seconds',99)]:
            profile=plan();profile[field]=value;backend=Backend()
            with self.assertRaises(ValueError):module.transaction(profile,backend)
            self.assertEqual(backend.calls,[])
        for key in module.REQUIRED_REVIEWS:
            profile=plan();profile['review'][key]=False;backend=Backend()
            with self.assertRaises(ValueError):module.transaction(profile,backend)
            self.assertEqual(backend.calls,[])
        for role,path in [('driver','/opt/servo_controller'),('sdk','/bin/sh')]:
            profile=plan();profile[role]['path']=path
            with self.assertRaises(ValueError):module.transaction(profile,Backend())
        profile=plan();profile['driver']['pid']=1
        with self.assertRaises(ValueError):module.transaction(profile,Backend())

    def test_preflight_failure_never_stops_or_attempts_restart(self):
        backend=Backend('preflight')
        with self.assertRaises(RuntimeError):module.transaction(plan(),backend)
        self.assertEqual(backend.calls,['preflight'])

    def test_success_and_all_post_stop_failures_attempt_restore(self):
        for fail in [None,'stop','exclusive','sdk']:
            backend=Backend(fail);report=module.transaction(plan(),backend)
            self.assertEqual(backend.calls[-1],'restore')
            self.assertTrue(report['driver_restored'])
            if fail in ('stop','exclusive'):self.assertNotIn('sdk',backend.calls)

    def test_timeout_restores_driver_and_reports_sdk_failure(self):
        backend=Backend()
        backend.run_sdk=lambda timeout,snapshot:{'exit_code':-9,'timed_out':True}
        report=module.transaction(plan(),backend)
        self.assertTrue(report['driver_restored']);self.assertTrue(report['sdk']['timed_out'])
        self.assertEqual(report['status'],'sdk_failed')

    def test_restore_failure_is_explicit_not_success(self):
        report=module.transaction(plan(),Backend('restore'))
        self.assertFalse(report['driver_restored'])
        self.assertEqual(report['status'],'RESTORE_FAILED_OPERATOR_REQUIRED')

    def test_no_live_broker_or_sudoers_permission_is_added(self):
        for name in ['deploy/runner/readonly.py','deploy/runner/readonly.sudoers','.github/workflows/pi-diagnostics.yml']:
            self.assertNotIn('exclusive_sdk',(ROOT/name).read_text())
