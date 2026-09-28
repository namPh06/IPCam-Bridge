import time
import unittest
from unittest.mock import patch

import numpy as np

from ip_camera_bridge.cameras import CameraGroup
from ip_camera_bridge.sources import SourceSpec
from test_controller import wait_until, hold_output


def solid_source(spec, shared, fps, statuses, stop):
    color = (255, 0, 0) if spec.address == 'red' else (0, 0, 255)
    frame = np.empty((shared.height, shared.width, 3), dtype=np.uint8)
    frame[:] = color
    shared.publish(frame)
    statuses.put(('connected', 'Test'))
    while not stop.wait(0.04):
        shared.publish(frame)


class MultiCameraTests(unittest.TestCase):
    def test_switch_keeps_publisher_alive_and_disconnect_is_isolated(self):
        group = CameraGroup(160, 90)
        group.add()
        with patch('ip_camera_bridge.controller.run_source', solid_source), patch('ip_camera_bridge.cameras.run_output', hold_output):
            try:
                for camera, address in zip(group.cameras, ('red', 'blue')):
                    camera.connect(SourceSpec(address=address))
                wait_until(lambda: all(camera.source_state == 'connected' for camera in group.cameras), group.tick)
                group.start_output()
                wait_until(lambda: group.output_state == 'running', group.tick)
                publisher_pid = group.output.process.pid
                np.testing.assert_array_equal(group.shared.read()[2][0, 0], (255, 0, 0))
                group.select(1)
                np.testing.assert_array_equal(group.shared.read()[2][0, 0], (0, 0, 255))
                self.assertEqual(group.output.process.pid, publisher_pid)
                group.cameras[0].disconnect()
                wait_until(lambda: not group.cameras[0].source.active, group.tick)
                self.assertEqual(group.cameras[1].source_state, 'connected')
                group.select(0)
                self.assertFalse(np.array_equal(group.shared.read()[2][0, 0], (0, 0, 255)))
                group.select(1)
                group.cameras[0].connect(SourceSpec(address='red'))
                wait_until(lambda: group.cameras[0].source_state == 'connected', group.tick)
                self.assertEqual(group.output.process.pid, publisher_pid)
            finally:
                group.close()
                wait_until(lambda: group.closed, group.tick)
            self.assertFalse(any(camera.source.active for camera in group.cameras))
            self.assertFalse(group.output.active)

    def test_automatic_output_waits_for_selected_camera_and_can_be_cancelled(self):
        group = CameraGroup(160, 90)
        with patch('ip_camera_bridge.cameras.run_output', hold_output):
            try:
                group.auto_output = True
                group.tick()
                self.assertFalse(group.output.active)
                group.stop_output()
                group.cameras[0].connect(SourceSpec())
                wait_until(lambda: group.current.source_state == 'connected', group.tick)
                self.assertFalse(group.output.active)
                group.auto_output = True
                wait_until(lambda: group.output_state == 'running', group.tick)
                self.assertFalse(group.auto_output)
                with self.assertRaises(RuntimeError):
                    group.set_resolution(320, 180)
                with self.assertRaises(RuntimeError):
                    group.remove(0)
            finally:
                group.close()
                wait_until(lambda: group.closed, group.tick)
