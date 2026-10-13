import ast
import json
import os
from pathlib import Path
import re
import tempfile
import traceback
import types
import unittest
from unittest.mock import MagicMock

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'deploy/runner/container_diagnostics.py').read_text()

def definitions(namespace):
    namespace.setdefault('traceback',traceback)
    namespace.setdefault('sdk_inventory',lambda processes:{'status':'complete','truncated':False})
    tree=ast.parse(SOURCE)
    names={'safe_text','process_inventory','usb_inventory','device_visibility','shared_memory_visibility','component_membership','probe_section','sdk_inventory','aurora_discover'}
    constants={'OFFICIAL_PACKAGE','OFFICIAL_LAUNCH','CAMERA_NODES','SDK_ROOTS'}
    selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in constants for t in n.targets)]
    exec(compile(ast.Module(body=selected,type_ignores=[]),'observer','exec'),namespace)
    if 'Path' not in namespace:
        namespace['sdk_inventory']=lambda processes:{'status':'complete','truncated':False}
    return namespace

class DiscoveryTests(unittest.TestCase):
    def test_official_launch_descendants_are_candidates_not_restart_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            proc=Path(directory)/'proc';proc.mkdir()
            for pid,parent,args,exe in [(1,0,['init'],'/sbin/init'),(7,1,['python3','/opt/ros/humble/bin/ros2','launch','deptrum-ros-driver-aurora930','aurora930_launch.py','token:=SECRET'],'/usr/bin/python3'),(8,7,['deptrum_node','--ros-args','-r','__node:=depth_cam'],'/opt/vendor/deptrum_node'),(9,1,['servo_controller'],'/opt/robot/servo'),(10,8,['component_container'],'/opt/ros/humble/lib/rclcpp_components/component_container')]:
                p=proc/str(pid);p.mkdir()
                (p/'status').write_text(f'Name:\t{Path(exe).name}\nPPid:\t{parent}\nUid:\t0 0 0 0\nThreads:\t2\nState:\tS (sleeping)\n')
                (p/'cmdline').write_bytes(('\0'.join(args)+'\0').encode())
                (p/'exe').symlink_to(exe)
            def mapped(value):return proc if value=='/proc' else Path(value)
            ns=definitions({'Path':mapped,'os':os,'re':re})
            records=ns['process_inventory']()
            self.assertEqual([r['pid'] for r in records],[7,8,10])
            self.assertTrue(records[0]['official_launch'])
            self.assertTrue(records[-1]['component_container'])
            self.assertTrue(all(r['node_pid_mapping_proven'] is False for r in records))
            self.assertNotIn('SECRET',json.dumps(records))
            self.assertNotIn('cmdline',records[0])

    def test_usb_presence_is_read_only_and_excludes_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            usb=Path(directory);device=usb/'1-1';device.mkdir()
            for name,value in {'idVendor':'1234','idProduct':'5678','authorized':'0','product':'Aurora','serial':'SECRET'}.items():(device/name).write_text(value)
            def mapped(value):return usb if value=='/sys/bus/usb/devices' else Path(value)
            ns=definitions({'Path':mapped,'os':os,'re':re})
            before={p.name:p.read_bytes() for p in device.iterdir()}
            result=ns['usb_inventory']()
            self.assertEqual(result['devices'][0]['authorized'],'0')
            self.assertNotIn('SECRET',json.dumps(result))
            self.assertEqual(before,{p.name:p.read_bytes() for p in device.iterdir()})

    def test_even_camera_lifecycle_services_do_not_enable_recovery(self):
        node=MagicMock()
        node.get_node_names_and_namespaces.return_value=[('depth_cam','/'),('servo','/')]
        node.get_service_names_and_types.return_value=[]
        node.get_service_names_and_types_by_node.return_value=[('/depth_cam/change_state',['lifecycle_msgs/srv/ChangeState'])]
        node.get_publishers_info_by_topic.return_value=[types.SimpleNamespace(node_name='depth_cam',node_namespace='/')]
        node.count_publishers.return_value=1
        ticks=iter(range(100))
        ns=definitions({'node':node,'time':types.SimpleNamespace(monotonic=lambda:next(ticks)),
                        'rclpy':types.SimpleNamespace(spin_once=lambda *a,**k:None),
                        'HEALTH_TOPICS':{'rgb':'/depth_cam/rgb/image_raw','depth':'/depth_cam/depth/image_raw','ir':'/depth_cam/ir/image_raw'},
                        'os':os,'get_rmw_implementation_identifier':lambda:'rmw_fastrtps_cpp'})
        ns.update(process_inventory=lambda:[],usb_inventory=lambda:{},device_visibility=lambda:{},shared_memory_visibility=lambda:[])
        result=ns['aurora_discover']()
        self.assertTrue(result['nodes'][0]['has_lifecycle_services'])
        self.assertFalse(result['recovery']['enabled'])
        self.assertEqual(result['recovery']['status'],'refused')
        node.create_client.assert_not_called()
        node.create_publisher.assert_not_called()
        node.get_service_names_and_types_by_node.side_effect=RuntimeError('Camera node left graph')
        ticks=iter(range(100))
        partial=ns['aurora_discover']()
        self.assertEqual(partial['status'],'partial')
        self.assertEqual(partial['nodes'][0]['services_status'],'unavailable')
        self.assertIsNone(partial['nodes'][0]['has_lifecycle_services'])
        self.assertFalse(partial['recovery']['enabled'])

    def test_observer_contains_no_mutating_process_or_usb_interface(self):
        for forbidden in ['os.kill(', 'subprocess.', 'ChangeState.Request', 'LoadNode', 'UnloadNode', 'USBDEVFS_RESET', 'os.system(']:
            self.assertNotIn(forbidden,SOURCE)

    def test_component_membership_uses_only_list_nodes_and_flags_shared_manager(self):
        import sys
        from unittest.mock import patch
        class ListNodes:
            class Request:pass
        module=types.ModuleType('composition_interfaces.srv');module.ListNodes=ListNodes
        future=MagicMock();future.done.return_value=True;future.exception.return_value=None
        future.result.return_value=types.SimpleNamespace(full_node_names=['/depth_cam','/servo_controller'],unique_ids=[1,2])
        client=MagicMock();client.wait_for_service.return_value=True;client.call_async.return_value=future
        node=MagicMock();node.create_client.return_value=client
        ticks=iter(range(100));ros=MagicMock()
        ns=definitions({'node':node,'time':types.SimpleNamespace(monotonic=lambda:next(ticks)),'rclpy':ros})
        with patch.dict(sys.modules,{'composition_interfaces':types.ModuleType('composition_interfaces'),'composition_interfaces.srv':module}):
            report=ns['component_membership']([('/container/_container/list_nodes',['composition_interfaces/srv/ListNodes'])])
        self.assertTrue(report[0]['includes_known_camera'])
        self.assertTrue(report[0]['other_nodes_present'])
        node.create_client.assert_called_once_with(ListNodes,'/container/_container/list_nodes')
        self.assertIsInstance(client.call_async.call_args.args[0],ListNodes.Request)
        node.destroy_client.assert_called_once_with(client)

    def test_host_usb_inventory_excludes_serial_and_reports_interface_driver(self):
        import importlib.util
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('discovery_broker',ROOT/'deploy/runner/readonly.py')
        broker=importlib.util.module_from_spec(spec);spec.loader.exec_module(broker)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);device=root/'1-1';device.mkdir()
            (device/'idVendor').write_text('1234');(device/'idProduct').write_text('5678')
            (device/'serial').write_text('DO_NOT_EXPORT')
            interface=root/'1-1:1.0';interface.mkdir();(interface/'driver').symlink_to('/sys/bus/usb/drivers/uvcvideo')
            def mapped(value):return root if value=='/sys/bus/usb/devices' else Path(value)
            with patch.object(broker,'Path',side_effect=mapped):report=broker.host_usb_inventory()
            self.assertEqual(report['devices'][0]['interfaces'][0]['driver'],'uvcvideo')
            self.assertNotIn('DO_NOT_EXPORT',json.dumps(report))

    def test_permission_and_hotplug_errors_return_partial_probe_evidence(self):
        ns=definitions({})
        for failure in [PermissionError(13,'USB directory denied'),FileNotFoundError(2,'USB device unplugged'),RuntimeError('Shared memory inventory limit exceeded')]:
            errors=[]
            def fail():raise failure
            result=ns['probe_section']('container_devices',fail,{'devices':[]},errors)
            self.assertEqual(result,{'devices':[]})
            self.assertEqual(errors[0]['error_type'],type(failure).__name__)
            self.assertIn(str(failure),errors[0]['traceback'])
            self.assertEqual(errors[0]['section'],'container_devices')

    def test_independent_discovery_survives_failed_optional_usb_probe(self):
        node=MagicMock();node.get_node_names_and_namespaces.return_value=[]
        node.get_service_names_and_types.return_value=[]
        node.get_publishers_info_by_topic.return_value=[];node.count_publishers.return_value=0
        ticks=iter(range(100))
        ns=definitions({'node':node,'time':types.SimpleNamespace(monotonic=lambda:next(ticks)),
                        'rclpy':types.SimpleNamespace(spin_once=lambda *a,**k:None),
                        'HEALTH_TOPICS':{'rgb':'/depth_cam/rgb/image_raw'},'os':os,
                        'get_rmw_implementation_identifier':lambda:'rmw_fastrtps_cpp'})
        def denied():raise PermissionError(13,'USB directory denied')
        ns.update(process_inventory=lambda:[{'pid':7}],usb_inventory=lambda:{'visible':False},device_visibility=denied,shared_memory_visibility=lambda:[])
        result=ns['aurora_discover']()
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['processes'],[{'pid':7}])
        self.assertEqual(result['probe_errors'][0]['section'],'container_devices')
        self.assertFalse(result['recovery']['enabled'])

    def test_large_graph_filters_camera_before_output_limits_and_marks_scan_truncation(self):
        for size in [300,5000]:
            node=MagicMock()
            entries=[('unrelated_'+str(i),'/') for i in range(size)]
            entries.insert(250,('depth_cam','/'))
            entries.append(('depth_cam','/'))
            node.get_node_names_and_namespaces.return_value=entries
            node.get_service_names_and_types.return_value=[('/unrelated_'+str(i),['std_srvs/srv/Trigger']) for i in range(size)]
            node.get_service_names_and_types_by_node.return_value=[]
            node.get_publishers_info_by_topic.return_value=[types.SimpleNamespace(node_name='depth_cam',node_namespace='/')]
            node.count_publishers.return_value=1
            ticks=iter(range(100))
            ns=definitions({'node':node,'time':types.SimpleNamespace(monotonic=lambda:next(ticks)),
                            'rclpy':types.SimpleNamespace(spin_once=lambda *a,**k:None),
                            'HEALTH_TOPICS':{'rgb':'/depth_cam/rgb/image_raw'},'os':os,
                            'get_rmw_implementation_identifier':lambda:'rmw_fastrtps_cpp'})
            ns.update(process_inventory=lambda:[{'pid':7}],usb_inventory=lambda:{'visible':True,'devices':[{'product':'Aurora'}]},device_visibility=lambda:{},shared_memory_visibility=lambda:[])
            report=ns['aurora_discover']()
            self.assertEqual([n['name'] for n in report['nodes']],['/depth_cam'])
            self.assertEqual(report['graph']['total_services'],size)
            self.assertLessEqual(report['graph']['scanned_services'],4096)
            self.assertEqual(report['status'],'partial' if size>4096 else 'complete')
            self.assertEqual(report['graph']['service_scan_truncated'],size>4096)
            self.assertEqual(report['processes'],[{'pid':7}])
            self.assertEqual(report['container_usb']['devices'][0]['product'],'Aurora')
            self.assertFalse(report['recovery']['enabled'])
            node.get_service_names_and_types_by_node.assert_called_once_with('depth_cam','/')

    def test_large_component_graph_prioritizes_camera_manager_and_keeps_ids_aligned(self):
        import sys
        from unittest.mock import patch
        class ListNodes:
            class Request:pass
        module=types.ModuleType('composition_interfaces.srv');module.ListNodes=ListNodes
        names=['/noise_'+str(i) for i in range(250)]+['/depth_cam']
        future=MagicMock();future.done.return_value=True;future.exception.return_value=None
        future.result.return_value=types.SimpleNamespace(full_node_names=names,unique_ids=list(range(251)))
        client=MagicMock();client.wait_for_service.return_value=True;client.call_async.return_value=future
        node=MagicMock();node.create_client.return_value=client
        ns=definitions({'node':node,'time':types.SimpleNamespace(monotonic=lambda:0),'rclpy':MagicMock()})
        services=[('/manager_'+str(i)+'/list_nodes',['composition_interfaces/srv/ListNodes']) for i in range(30)]
        services.append(('/depth_cam/_container/list_nodes',['composition_interfaces/srv/ListNodes']))
        with patch.dict(sys.modules,{'composition_interfaces':types.ModuleType('composition_interfaces'),'composition_interfaces.srv':module}):
            report=ns['component_membership'](services)
        self.assertEqual(len(report),16)
        self.assertEqual(report[0]['service'],'/depth_cam/_container/list_nodes')
        self.assertEqual(report[0]['status'],'truncated')
        self.assertEqual(len(report[0]['nodes']),200)
        self.assertEqual(report[0]['nodes'][0],'/depth_cam')
        self.assertEqual(report[0]['component_ids'][0],250)
        self.assertTrue(report[0]['includes_known_camera'])
        self.assertEqual(node.destroy_client.call_count,16)

    def test_sdk_inventory_reports_metadata_and_loaded_library_names_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);sdk=root/'deptrum-sdk';sdk.mkdir()
            binary=sdk/'sdk_sample';binary.write_bytes(b'NOT EXECUTED');binary.chmod(0o755)
            library=sdk/'libdeptrum.so.2';library.write_bytes(b'NOT LOADED')
            outside=root/'secret';outside.write_text('DO_NOT_READ')
            (sdk/'deptrum_link').symlink_to(outside)
            proc=root/'proc'/'7';proc.mkdir(parents=True)
            (proc/'maps').write_text('1234-2345 r-xp 0000 00:00 0 /opt/deptrum/libdeptrum.so.2\n1234-2345 r-xp 0000 00:00 0 /secret/non_sdk.so\n')
            def mapped(value):return root/'proc' if value=='/proc' else Path(value)
            ns=definitions({'Path':mapped,'os':os,'time':types.SimpleNamespace(monotonic=lambda:0)})
            ns['SDK_ROOTS']=(str(root),)
            result=ns['sdk_inventory']([{'pid':7}])
            self.assertFalse(result['sdk_executed']);self.assertFalse(result['camera_opened'])
            self.assertTrue(any(f['path']==str(binary) and f['test_name_hint'] and not f['safe_to_run_proven'] for f in result['files']))
            self.assertEqual(result['loaded_library_filenames'],[{'pid':7,'path':'/opt/deptrum/libdeptrum.so.2'}])
            self.assertNotIn('DO_NOT_READ',json.dumps(result))
            self.assertNotIn('NOT EXECUTED',json.dumps(result))
            self.assertEqual(binary.read_bytes(),b'NOT EXECUTED')

    def test_sdk_inventory_caps_large_tree_and_marks_unknown_loaded_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for i in range(4200):(root/('unrelated_'+str(i))).touch()
            ns=definitions({'Path':Path,'os':os,'time':types.SimpleNamespace(monotonic=lambda:0)})
            ns['SDK_ROOTS']=(str(root),)
            result=ns['sdk_inventory']([{'pid':99999999}])
            self.assertTrue(result['truncated'])
            self.assertEqual(result['status'],'partial')
            self.assertLessEqual(result['entries_examined'],4096)
            self.assertEqual(result['issues'][0]['error_type'],'unreadable')
