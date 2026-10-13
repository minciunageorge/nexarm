"""Fixed transient ROS observer. No shell, actuator publishers, driver launch or image-file output."""
import base64
import ctypes
import faulthandler
import json
import os
from pathlib import Path
import re
import resource
import signal
import sys
import time
import traceback

# Default SIGALRM terminates even if middleware/native code blocks; killing the
# Docker client alone would otherwise leave docker exec running in the container.
faulthandler.register(signal.SIGALRM, file=sys.stderr, all_threads=False, chain=True)
signal.alarm(22)
resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024,512*1024*1024))
resource.setrlimit(resource.RLIMIT_CPU, (15,15))
if sys.version_info[:2] != (3,10):
    raise SystemExit('Expected ROS Humble Python 3.10')
for path in ['/opt/ros/humble/local/lib/python3.10/dist-packages','/opt/ros/humble/lib/python3.10/site-packages']:
    sys.path.insert(0,path)
import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
from rclpy.utilities import get_rmw_implementation_identifier
from sensor_msgs.msg import Image

RGB_TOPIC = '/depth_cam/rgb/image_raw'
# Primary reader matches the live Aurora RELIABLE/VOLATILE writer. A bounded
# best-effort fallback preserves support for sensor-data writers on this topic.
CAMERA_QOS = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=5,
                        reliability=ReliabilityPolicy.RELIABLE,
                        durability=DurabilityPolicy.VOLATILE)
FALLBACK_QOS = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=5,
                          reliability=ReliabilityPolicy.BEST_EFFORT,
                          durability=DurabilityPolicy.VOLATILE)


# Humble's Python Subscription wrapper lacks get_publisher_count(). Borrow the
# protected rcl_subscription_t pointer for this fixed read-only RCL query.
_rcl = ctypes.CDLL('/opt/ros/humble/lib/librcl.so')
_count_matched = _rcl.rcl_subscription_get_publisher_count
_count_matched.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
_count_matched.restype = ctypes.c_int


def matched_publishers(subscription):
    count = ctypes.c_size_t()
    handle = subscription.handle
    with handle:
        status = _count_matched(ctypes.c_void_p(handle.pointer), ctypes.byref(count))
    if status != 0:
        raise RuntimeError('RCL matched-publisher query failed')
    return count.value


def describe_qos(qos):
    return {'reliability': qos.reliability.name, 'durability': qos.durability.name,
            'history': qos.history.name, 'depth': qos.depth,
            'deadline_ns': qos.deadline.nanoseconds,
            'lifespan_ns': qos.lifespan.nanoseconds,
            'liveliness': qos.liveliness.name,
            'liveliness_lease_duration_ns': qos.liveliness_lease_duration.nanoseconds}


def camera_publishers():
    endpoints = node.get_publishers_info_by_topic(RGB_TOPIC)
    if len(endpoints) > 200:
        raise RuntimeError('Camera publisher limit exceeded')
    return {'topic': RGB_TOPIC, 'publisher_count': node.count_publishers(RGB_TOPIC),
            'publishers': [{'node_name': endpoint.node_name,
                            'node_namespace': endpoint.node_namespace,
                            'topic_type': endpoint.topic_type,
                            'offered_qos': describe_qos(endpoint.qos_profile)}
                           for endpoint in endpoints],
            'requested_qos': describe_qos(CAMERA_QOS),
            'fallback_requested_qos': describe_qos(FALLBACK_QOS)}


HEALTH_TOPICS = {'rgb': RGB_TOPIC, 'depth': '/depth_cam/depth/image_raw',
                 'ir': '/depth_cam/ir/image_raw'}


def aurora_health():
    # Only these three exact Image topics. No parameters, launch, USB/device
    # queries, driver counters or actuator publishers. Image pixels are discarded.
    streams = {}
    subscriptions = []
    started = time.monotonic()
    try:
        for channel, topic in HEALTH_TOPICS.items():
            readers = []
            streams[channel] = {'topic': topic, 'readers': readers}
            for reliability in (ReliabilityPolicy.RELIABLE, ReliabilityPolicy.BEST_EFFORT):
                stats = {'reliability': reliability.name, 'durability': 'VOLATILE',
                         'messages_received': 0, 'first_stamp': None, 'last_stamp': None,
                         'stamp_advances': 0, 'stamp_repeats': 0, 'stamp_regressions': 0,
                         'first_arrival_ms': None, 'last_arrival_ms': None,
                         'max_matched_publishers': 0, 'matched_publishers': 0}
                readers.append(stats)
                def receive(message, stats=stats):
                    stamp = [message.header.stamp.sec, message.header.stamp.nanosec]
                    arrival = round((time.monotonic()-started)*1000)
                    if stats['messages_received']:
                        previous = stats['last_stamp']
                        field = 'stamp_advances' if stamp > previous else 'stamp_repeats' if stamp == previous else 'stamp_regressions'
                        stats[field] += 1
                    else:
                        stats['first_stamp'] = stamp
                        stats['first_arrival_ms'] = arrival
                    stats['messages_received'] += 1
                    stats['last_stamp'] = stamp
                    stats['last_arrival_ms'] = arrival
                qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1,
                                 reliability=reliability, durability=DurabilityPolicy.VOLATILE)
                subscription = node.create_subscription(Image, topic, receive, qos)
                subscriptions.append((subscription, stats))
        deadline = started+10
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            for subscription, stats in subscriptions:
                matched = matched_publishers(subscription)
                stats['matched_publishers'] = matched
                stats['max_matched_publishers'] = max(stats['max_matched_publishers'], matched)
        for channel, stream in streams.items():
            count = node.count_publishers(stream['topic'])
            stream['publisher_count'] = count
            readers = stream['readers']
            stream['messages_observed'] = any(reader['messages_received'] for reader in readers)
            stream['counter_advancing'] = any(reader['messages_received'] >= 2 for reader in readers)
            stream['timestamp_advancing'] = any(reader['stamp_advances'] for reader in readers)
            stream['status'] = ('messages_observed' if stream['messages_observed'] else
                                'matched_no_messages' if any(reader['max_matched_publishers'] for reader in readers) else
                                'discovered_unmatched' if count else 'no_publisher_discovered')
        other_flow = streams['depth']['messages_observed'] or streams['ir']['messages_observed']
        summary = ('rgb_silent_other_streams_delivering' if not streams['rgb']['messages_observed'] and other_flow else
                   'all_observed_streams_silent' if not any(s['messages_observed'] for s in streams.values()) else
                   'messages_observed')
        return {'operation': 'aurora-health', 'observation_ms': round((time.monotonic()-started)*1000),
                'ros_domain_id': os.environ.get('ROS_DOMAIN_ID','0'),
                'rmw': get_rmw_implementation_identifier(), 'streams': streams,
                'summary': summary, 'counters_are': 'observer_callbacks_per_reader_not_driver_counters'}
    finally:
        for subscription, _ in subscriptions:
            node.destroy_subscription(subscription)


OFFICIAL_PACKAGE = 'deptrum-ros-driver-aurora930'
OFFICIAL_LAUNCH = 'aurora930_launch.py'
CAMERA_NODES = ('/aurora', '/depth_cam')


def safe_text(path, limit=4096):
    try:
        with path.open('rb') as source:
            return source.read(limit).decode('utf-8', errors='replace')
    except OSError:
        return None


def process_inventory():
    records = {}
    # No environ, full command line, descriptors or memory contents exported.
    # Arguments are examined only for the known launch signature/remap hints.
    entries = sorted((p for p in Path('/proc').iterdir() if p.name.isdecimal()), key=lambda p:int(p.name))
    if len(entries)>1024: raise RuntimeError('Process inventory limit exceeded')
    for path in entries:
        status = safe_text(path/'status')
        cmdline = safe_text(path/'cmdline', 16384)
        if status is None: continue
        fields = dict(line.split(':',1) for line in status.splitlines() if ':' in line)
        arguments = (cmdline or '').split('\x00')
        official = any(i>0 and Path(arguments[i-1]).name=='ros2' and arguments[i:i+3]==['launch',OFFICIAL_PACKAGE,OFFICIAL_LAUNCH]
                       for i in range(max(0,len(arguments)-2)))
        try:
            executable = os.readlink(path/'exe')
        except OSError:
            executable = None
        basename = Path(executable).name if executable else fields.get('Name','').strip()
        component = basename in ('component_container','component_container_mt','component_container_isolated')
        camera_hint = official or any('deptrum' in value.lower() or 'aurora930' in value.lower()
                                     for value in [basename,*(arguments[:2])])
        camera_hint = camera_hint or any(value in ('__node:=depth_cam','__node:=aurora') for value in arguments)
        try:
            record = {'pid':int(path.name), 'ppid':int(fields.get('PPid','0')),
                      'uid':int(fields.get('Uid','-1').split()[0]),
                      'state':fields.get('State','unknown').strip()[:64],
                      'threads':int(fields.get('Threads','0')),
                      'name':fields.get('Name','').strip()[:64],
                      'executable':executable, 'official_launch':official,
                      'component_container':component, 'camera_hint':camera_hint,
                      'argv_readable':cmdline is not None, 'node_pid_mapping_proven':False}
        except ValueError: continue
        record['wait_channel']=safe_text(path/'wchan',64)
        raw_stat=safe_text(path/'stat',4096)
        if raw_stat:
            try:record['start_ticks']=int(raw_stat.rsplit(')',1)[1].split()[19])
            except (ValueError,IndexError):pass
        if official:record['launch_signature']=['ros2','launch',OFFICIAL_PACKAGE,OFFICIAL_LAUNCH]
        records[record['pid']] = record
    selected = {pid for pid,record in records.items() if record['camera_hint'] or record['component_container']}
    # Include descendants, bounded by the same inventory cap; never signal them.
    for _ in range(len(records)):
        descendants={pid for pid,record in records.items() if record['ppid'] in selected}
        if descendants<=selected:break
        selected.update(descendants)
    if len(selected)>64: raise RuntimeError('Camera process candidate limit exceeded')
    return [records[pid] for pid in sorted(selected)]


def usb_inventory():
    devices=[]
    root=Path('/sys/bus/usb/devices')
    if not root.exists():return {'visible':False,'devices':[]}
    entries=sorted(root.iterdir(),key=lambda path:path.name)
    if len(entries)>256:raise RuntimeError('USB inventory limit exceeded')
    for path in entries:
        vendor=safe_text(path/'idVendor',16);product=safe_text(path/'idProduct',16)
        if vendor is None or product is None:continue
        vendor=vendor.strip();product=product.strip()
        if not re.fullmatch('[0-9a-fA-F]{4}',vendor) or not re.fullmatch('[0-9a-fA-F]{4}',product):continue
        info={'sysfs_name':path.name,'vendor_id':vendor,'product_id':product}
        for key in ('manufacturer','product','authorized','busnum','devnum','speed','bConfigurationValue'):
            value=safe_text(path/key,128)
            if value is not None:info[key]=value.strip()
        try:info['driver']=Path(os.readlink(path/'driver')).name
        except OSError:info['driver']=None
        devices.append(info)
    return {'visible':True,'devices':devices,
            'limitations':'Presence/authorized/binding only; no transfer, reset, unbind or kernel-log access'}


def device_visibility():
    root=Path('/dev/bus/usb');devices=[]
    if root.exists():
        for bus in sorted(root.iterdir())[:64]:
            if not bus.name.isdecimal() or not bus.is_dir():continue
            for path in sorted(bus.iterdir())[:128]:
                if not path.name.isdecimal():continue
                info=path.stat()
                devices.append({'path':str(path),'uid':info.st_uid,'gid':info.st_gid,
                                'mode':oct(info.st_mode & 0o777),
                                'observer_readable':os.access(path,os.R_OK),
                                'observer_writable':os.access(path,os.W_OK)})
                if len(devices)>=128: return {'devices':devices,'truncated':True}
    return {'devices':devices,'truncated':False}


def shared_memory_visibility():
    # Metadata only, for diagnosing a root-owned writer vs UID 65534 DDS reader.
    root=Path('/dev/shm');items=[]
    if not root.exists():return []
    entries=sorted(root.iterdir(),key=lambda path:path.name)
    if len(entries)>256:raise RuntimeError('Shared memory inventory limit exceeded')
    for path in entries:
        if not path.name.startswith(('fastrtps','fastdds')):continue
        try:info=path.lstat()
        except OSError:continue
        items.append({'name':path.name,'uid':info.st_uid,'gid':info.st_gid,
                      'mode':oct(info.st_mode & 0o777),'mtime_ns':info.st_mtime_ns,
                      'observer_readable':os.access(path,os.R_OK),
                      'observer_writable':os.access(path,os.W_OK)})
    return items


def component_membership(services):
    managers=[name for name,types in services if 'composition_interfaces/srv/ListNodes' in types]
    if len(managers)>16:raise RuntimeError('Component manager limit exceeded')
    if not managers:return []
    try:
        from composition_interfaces.srv import ListNodes
    except ImportError:
        return [{'service':name,'status':'module_unavailable'} for name in managers]
    results=[];deadline=time.monotonic()+4
    for name in managers:
        if time.monotonic()>=deadline:
            results.append({'service':name,'status':'budget_exhausted'});continue
        client=node.create_client(ListNodes,name)
        try:
            if not client.wait_for_service(timeout_sec=0.1):
                results.append({'service':name,'status':'unavailable'});continue
            future=client.call_async(ListNodes.Request())
            rclpy.spin_until_future_complete(node,future,timeout_sec=max(0,deadline-time.monotonic()))
            if not future.done() or future.exception() is not None:
                results.append({'service':name,'status':'timeout_or_error'});continue
            response=future.result()
            if len(response.full_node_names)>200 or len(response.unique_ids)!=len(response.full_node_names):
                raise RuntimeError('Component membership limit/shape exceeded')
            results.append({'service':name,'status':'reported','nodes':list(response.full_node_names),
                            'component_ids':list(response.unique_ids),
                            'includes_known_camera':any(n in CAMERA_NODES for n in response.full_node_names),
                            'other_nodes_present':any(n not in CAMERA_NODES for n in response.full_node_names)})
        finally:node.destroy_client(client)
    return results


def probe_section(name, function, fallback, errors):
    """Optional probes cannot discard independent discovery evidence."""
    try:return function()
    except (OSError,RuntimeError,ValueError,IndexError,AttributeError) as error:
        errors.append({'section':name,'error_type':type(error).__name__,
                       'error':str(error)[:4096], 'traceback':traceback.format_exc(limit=6)[-8192:]})
        return fallback


def aurora_discover():
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=0.1)
    nodes=node.get_node_names_and_namespaces()
    services=node.get_service_names_and_types()
    if len(nodes)>200 or len(services)>200:raise RuntimeError('Discovery graph limit exceeded')
    errors=[]
    camera_nodes=[]
    for name,namespace in nodes:
        full=namespace.rstrip('/')+'/'+name
        if full not in CAMERA_NODES:continue
        def services_for_node(name=name,namespace=namespace):
            offered=node.get_service_names_and_types_by_node(name,namespace)
            if len(offered)>64:raise RuntimeError('Camera service limit exceeded')
            return offered
        offered=probe_section('camera_services:'+full,services_for_node,[],errors)
        unavailable=any(error['section']=='camera_services:'+full for error in errors)
        camera_nodes.append({'name':full,'services':offered,
                             'services_status':'unavailable' if unavailable else 'reported',
                             'has_lifecycle_services':None if unavailable else any('lifecycle_msgs/srv/ChangeState' in types for _,types in offered)})
    publishers={}
    for channel,topic in HEALTH_TOPICS.items():
        endpoints=node.get_publishers_info_by_topic(topic)
        if len(endpoints)>64:raise RuntimeError('Camera endpoint limit exceeded')
        publishers[channel]={'topic':topic,'publisher_count':node.count_publishers(topic),
                             'nodes':[endpoint.node_namespace.rstrip('/')+'/'+endpoint.node_name for endpoint in endpoints]}
    components=probe_section('component_membership',lambda:component_membership(services),[],errors)
    processes=probe_section('processes',process_inventory,[],errors)
    usb=probe_section('container_usb',usb_inventory,{'visible':None,'devices':[]},errors)
    devices=probe_section('container_devices',device_visibility,{'devices':[],'truncated':False},errors)
    shared_memory=probe_section('dds_shared_memory',shared_memory_visibility,[],errors)
    return {'operation':'aurora-discover','nodes':camera_nodes,'camera_publishers':publishers,
            'component_manager_services':components, 'processes':processes,
            'container_usb':usb,'container_devices':devices,
            'observer_uid':os.geteuid(), 'dds_shared_memory':shared_memory,
            'status':'partial' if errors else 'complete', 'probe_errors':errors,
            'ros_domain_id':os.environ.get('ROS_DOMAIN_ID','0'),'rmw':get_rmw_implementation_identifier(),
            'recovery':{'enabled':False,'status':'refused',
                        'reason':'Live process/component ownership and camera-only restart mechanism are not yet proven',
                        'official_launch':['ros2','launch',OFFICIAL_PACKAGE,OFFICIAL_LAUNCH],
                        'container_restart_allowed':False,'process_signals_allowed':False}}


operation=sys.argv[1]
if operation not in ('ros-inspect','camera-one-frame','aurora-health','aurora-discover','aurora-recover'): raise SystemExit('Unsupported observer operation')
rclpy.init()
node=rclpy.create_node('nexarm_diagnostic_observer',enable_rosout=False,start_parameter_services=False)
try:
    if operation in ('aurora-discover','aurora-recover'):
        report=aurora_discover()
        if operation=='aurora-recover':
            report['operation']='aurora-recover'
            report['health']=aurora_health()
        print(json.dumps(report),flush=True)
    elif operation=='aurora-health':
        print(json.dumps(aurora_health()),flush=True)
    elif operation=='ros-inspect':
        deadline=time.monotonic()+3
        while time.monotonic()<deadline: rclpy.spin_once(node,timeout_sec=0.1)
        nodes=node.get_node_names_and_namespaces()
        topics=node.get_topic_names_and_types()
        if len(nodes)>200 or len(topics)>200: raise RuntimeError('Graph size limit exceeded')
        print(json.dumps({'nodes':sorted(nodes),'topics':sorted(topics), 'ros_domain_id':os.environ.get('ROS_DOMAIN_ID','0'), 'rmw':get_rmw_implementation_identifier(), 'camera':camera_publishers()}),flush=True)
    else:
        frame=[]
        attempts=[]
        deadline=time.monotonic()+15
        for qos in (CAMERA_QOS,FALLBACK_QOS):
            attempt={'reliability':qos.reliability.name, 'durability':qos.durability.name,
                     'max_matched_publishers':0, 'matched_publishers':0,
                     'messages_received':0}
            attempts.append(attempt)
            def receive(message):
                attempt['messages_received']+=1
                if not frame: frame.append(message)
            subscription=node.create_subscription(Image,RGB_TOPIC,receive,qos)
            try:
                attempt_deadline=min(deadline,time.monotonic()+8) if qos is CAMERA_QOS else deadline
                while not frame and time.monotonic()<attempt_deadline:
                    rclpy.spin_once(node,timeout_sec=0.1)
                    matched=matched_publishers(subscription)
                    attempt['matched_publishers']=matched
                    attempt['max_matched_publishers']=max(attempt['max_matched_publishers'],matched)
            finally:
                node.destroy_subscription(subscription)
            if frame: break
        publishers=camera_publishers()
        delivery={'topic':RGB_TOPIC, 'publisher_count':publishers['publisher_count'],
                  'offered_reliability':[p['offered_qos']['reliability'] for p in publishers['publishers']],
                  'offered_durability':[p['offered_qos']['durability'] for p in publishers['publishers']],
                  'attempts':attempts}
        if not frame:
            # Broker accepts only this bounded schema and exports it to the error
            # artifact; raw container stderr/tracebacks are never forwarded.
            print(json.dumps({'camera_timeout':delivery}),flush=True)
            raise TimeoutError('No Aurora RGB frame within 15 seconds')
        message=frame[0]
        if (message.width,message.height)!=(640,400) or message.encoding not in ('rgb8','bgr8'):
            raise ValueError('Expected a 640x400 rgb8/bgr8 Aurora frame')
        if not 1920<=message.step<=4096 or len(message.data)!=message.step*400:
            raise ValueError('Invalid/oversized image stride')
        pixels=bytearray()
        for y in range(400):
            row=bytes(message.data[y*message.step:y*message.step+1920])
            if message.encoding=='bgr8':
                row=bytes(value for x in range(0,len(row),3) for value in (row[x+2],row[x+1],row[x]))
            pixels.extend(row)
        print(json.dumps({'topic':'/depth_cam/rgb/image_raw','width':640,'height':400,
                          'encoding':'rgb8','ros_domain_id':os.environ.get('ROS_DOMAIN_ID','0'),'rmw':get_rmw_implementation_identifier(),'pixels':base64.b64encode(pixels).decode(),
                          'stamp_sec':message.header.stamp.sec,'stamp_nanosec':message.header.stamp.nanosec,
                          'frame_id':message.header.frame_id[:256], 'camera':publishers, 'selected_qos':describe_qos(qos), 'delivery':delivery}),flush=True)
finally:
    node.destroy_node();rclpy.shutdown()
