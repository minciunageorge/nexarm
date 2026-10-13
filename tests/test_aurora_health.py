"""Run the actual health observer against controlled message/timestamp streams."""
import ast
from pathlib import Path
import types
import unittest
from unittest.mock import MagicMock
from test_observer import Reliability, Durability, History, QoS

SOURCE=(Path(__file__).resolve().parents[1]/'deploy/runner/container_diagnostics.py').read_text()

class HealthTests(unittest.TestCase):
    def run_health(self,flowing=(),fixed_stamp=False,matched=1,publishers=1):
        clock=[0.0];subscriptions=[]
        node=MagicMock();node.count_publishers.return_value=publishers
        def subscribe(kind,topic,callback,qos):
            sub=types.SimpleNamespace(topic=topic,callback=callback)
            subscriptions.append(sub)
            self.assertIn(topic,['/depth_cam/rgb/image_raw','/depth_cam/depth/image_raw','/depth_cam/ir/image_raw'])
            self.assertEqual(qos.depth,1)
            return sub
        node.create_subscription.side_effect=subscribe
        def spin(*args,**kwargs):
            clock[0]+=1
            for sub in subscriptions:
                if sub.topic.split('/')[2] in flowing:
                    stamp=types.SimpleNamespace(sec=42 if fixed_stamp else int(clock[0]),nanosec=0)
                    sub.callback(types.SimpleNamespace(header=types.SimpleNamespace(stamp=stamp)))
        ros=types.SimpleNamespace(spin_once=spin)
        namespace={'node':node,'rclpy':ros,'time':types.SimpleNamespace(monotonic=lambda:clock[0]),
                   'RGB_TOPIC':'/depth_cam/rgb/image_raw','QoSProfile':QoS,'ReliabilityPolicy':Reliability,
                   'DurabilityPolicy':Durability,'HistoryPolicy':History,'Image':object,
                   'matched_publishers':lambda subscription:matched,
                   'os':types.SimpleNamespace(environ={}), 'get_rmw_implementation_identifier':lambda:'rmw_fastrtps_cpp'}
        tree=ast.parse(SOURCE)
        selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='aurora_health' or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HEALTH_TOPICS' for t in n.targets)]
        exec(compile(ast.Module(body=selected,type_ignores=[]),'observer','exec'),namespace)
        result=namespace['aurora_health']()
        self.assertEqual(node.create_subscription.call_count,6)
        self.assertEqual(node.destroy_subscription.call_count,6)
        node.create_publisher.assert_not_called();node.create_client.assert_not_called()
        return result

    def test_rgb_silence_is_distinct_from_advancing_depth_ir(self):
        result=self.run_health(flowing=('depth','ir'))
        self.assertEqual(result['summary'],'rgb_silent_other_streams_delivering')
        self.assertEqual(result['streams']['rgb']['status'],'matched_no_messages')
        for channel in ['depth','ir']:
            stream=result['streams'][channel]
            self.assertTrue(stream['counter_advancing']);self.assertTrue(stream['timestamp_advancing'])
            for reader in stream['readers']:
                self.assertEqual(reader['messages_received'],10)
                self.assertEqual(reader['stamp_advances'],9)
                self.assertLess(reader['first_stamp'],reader['last_stamp'])

    def test_frozen_stamps_do_not_hide_advancing_message_counters(self):
        result=self.run_health(flowing=('rgb','depth','ir'),fixed_stamp=True)
        for stream in result['streams'].values():
            self.assertTrue(stream['counter_advancing']);self.assertFalse(stream['timestamp_advancing'])
            self.assertEqual(stream['readers'][0]['stamp_repeats'],9)

    def test_all_matched_but_silent_is_not_usb_failure_claim(self):
        result=self.run_health()
        self.assertEqual(result['summary'],'all_observed_streams_silent')
        for stream in result['streams'].values():
            self.assertEqual(stream['status'],'matched_no_messages')
            self.assertIsNone(stream['readers'][0]['last_stamp'])
            self.assertEqual(stream['readers'][0]['messages_received'],0)

    def test_absent_and_unmatched_publishers_are_distinguished(self):
        for publishers,status in [(0,'no_publisher_discovered'),(1,'discovered_unmatched')]:
            result=self.run_health(matched=0,publishers=publishers)
            self.assertTrue(all(stream['status']==status for stream in result['streams'].values()))
