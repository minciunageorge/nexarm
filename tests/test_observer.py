"""Execute the fixed observer with fake ROS endpoints; never contact the Pi."""
import contextlib
from enum import IntEnum
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

SOURCE=(Path(__file__).resolve().parents[1]/'deploy/runner/container_diagnostics.py').read_text()

class Reliability(IntEnum):
    RELIABLE=1
    BEST_EFFORT=2
class Durability(IntEnum):
    TRANSIENT_LOCAL=1
    VOLATILE=2
class History(IntEnum):
    KEEP_LAST=1
class Liveliness(IntEnum):
    AUTOMATIC=1

class QoS:
    def __init__(self,*,history,depth,reliability,durability):
        self.history=history;self.depth=depth
        self.reliability=reliability;self.durability=durability
        self.deadline=self.lifespan=self.liveliness_lease_duration=types.SimpleNamespace(nanoseconds=0)
        self.liveliness=Liveliness.AUTOMATIC

class ObserverTests(unittest.TestCase):
    def observe(self,operation,receive=True,endpoints=None):
        node=MagicMock()
        node.get_node_names_and_namespaces.return_value=[('camera','/')]
        node.get_topic_names_and_types.return_value=[('/depth_cam/rgb/image_raw',['sensor_msgs/msg/Image'])]
        if endpoints is None:
            endpoints=[types.SimpleNamespace(node_name='aurora',node_namespace='/',topic_type='sensor_msgs/msg/Image',qos_profile=QoS(history=History.KEEP_LAST,depth=10,reliability=Reliability.BEST_EFFORT,durability=Durability.VOLATILE))]
        node.get_publishers_info_by_topic.return_value=endpoints
        node.count_publishers.return_value=len(endpoints)
        frame=types.SimpleNamespace(width=640,height=400,step=1920,encoding='rgb8',data=bytes([1,2,3])*(640*400),header=types.SimpleNamespace(stamp=types.SimpleNamespace(sec=1,nanosec=2),frame_id='rgb'))
        def subscribe(message_type,topic,callback,qos):
            self.assertEqual(topic,'/depth_cam/rgb/image_raw')
            self.assertEqual(qos.reliability,Reliability.BEST_EFFORT)
            self.assertEqual(qos.durability,Durability.VOLATILE)
            self.assertEqual(qos.history,History.KEEP_LAST)
            self.assertEqual(qos.depth,5)
            if receive:callback(frame)
        node.create_subscription.side_effect=subscribe
        ros=types.ModuleType('rclpy');ros.init=MagicMock();ros.create_node=MagicMock(return_value=node);ros.spin_once=MagicMock();ros.shutdown=MagicMock()
        qos=types.ModuleType('rclpy.qos');qos.QoSProfile=QoS;qos.ReliabilityPolicy=Reliability;qos.DurabilityPolicy=Durability;qos.HistoryPolicy=History
        utilities=types.ModuleType('rclpy.utilities');utilities.get_rmw_implementation_identifier=lambda:'rmw_fastrtps_cpp'
        messages=types.ModuleType('sensor_msgs.msg');messages.Image=object
        modules={'rclpy':ros,'rclpy.qos':qos,'rclpy.utilities':utilities,'sensor_msgs':types.ModuleType('sensor_msgs'),'sensor_msgs.msg':messages}
        output=io.StringIO()
        with patch.dict(sys.modules,modules),patch.object(sys,'argv',['observer',operation]),patch.object(sys,'version_info',(3,10)),patch.object(sys,'path',list(sys.path)),patch('signal.alarm'),patch('resource.setrlimit'),patch('time.monotonic',side_effect=range(100)),contextlib.redirect_stdout(output):
            try:exec(compile(SOURCE,'container_diagnostics.py','exec'),{})
            finally:
                node.destroy_node.assert_called_once();ros.shutdown.assert_called_once()
        return json.loads(output.getvalue()),node

    def test_graph_reports_exact_fixed_camera_count_and_offered_qos(self):
        result,node=self.observe('ros-inspect')
        camera=result['camera']
        self.assertEqual(camera['publisher_count'],1)
        self.assertEqual(camera['publishers'][0]['offered_qos']['reliability'],'BEST_EFFORT')
        self.assertEqual(camera['publishers'][0]['offered_qos']['durability'],'VOLATILE')
        node.get_publishers_info_by_topic.assert_called_once_with('/depth_cam/rgb/image_raw')
        node.count_publishers.assert_called_once_with('/depth_cam/rgb/image_raw')
        node.create_subscription.assert_not_called()

    def test_camera_uses_explicit_sensor_qos_and_exports_metadata(self):
        result,node=self.observe('camera-one-frame')
        self.assertEqual(result['camera']['requested_qos']['reliability'],'BEST_EFFORT')
        self.assertEqual(result['camera']['requested_qos']['durability'],'VOLATILE')
        self.assertEqual(result['camera']['publisher_count'],1)
        node.create_subscription.assert_called_once()
        node.create_publisher.assert_not_called()
        node.create_client.assert_not_called()

    def test_no_publisher_is_reported_without_camera_launch(self):
        result,node=self.observe('ros-inspect',endpoints=[])
        self.assertEqual(result['camera']['publisher_count'],0)
        self.assertEqual(result['camera']['publishers'],[])
        node.create_publisher.assert_not_called()

    def test_endpoint_limit_and_missing_frame_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError,'publisher limit'):
            self.observe('ros-inspect',endpoints=[None]*201)
        with self.assertRaisesRegex(TimeoutError,'No Aurora RGB frame'):
            self.observe('camera-one-frame',receive=False)
