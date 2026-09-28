import contextlib
import os
import queue
import sys
import types
import unittest
from unittest.mock import patch

from ip_camera_bridge import output

import numpy as np
from ip_camera_bridge.frames import status_frame


class ClockEvent:
    def __init__(self, iterations):
        self.now, self.iterations = 10.0, iterations
        self.waits = []

    def is_set(self):
        return len(self.waits) >= self.iterations

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        return self.is_set()


class Camera:
    def __init__(self, **kwargs):
        self.arguments = kwargs
        self.frames = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def send(self, frame):
        self.frames.append(frame.copy())


class OutputTests(unittest.TestCase):
    def test_rgb_output_is_paced_and_stale_input_becomes_slate(self):
        event = ClockEvent(128)
        frame = np.full((48, 64, 3), (255, 0, 0), dtype=np.uint8)
        shared = types.SimpleNamespace(width=64, height=48,
            read=lambda seq: (1, 10.0, frame.copy()) if seq < 1 else None)
        statuses = queue.Queue()
        camera = Camera()
        module = types.SimpleNamespace(Camera=lambda **kwargs: camera.__dict__.update(arguments=kwargs) or camera,
                                       PixelFormat=types.SimpleNamespace(RGB='RGB'))
        with patch.dict(sys.modules, {'pyvirtualcam': module}), \
             patch.object(output, '_publisher_mutex', return_value=contextlib.nullcontext()), \
             patch.object(output.time, 'monotonic', side_effect=lambda: event.now):
            output.run_output(shared, 25, statuses, event)
        self.assertTrue(camera.closed)
        self.assertEqual(camera.arguments, {'width': 64, 'height': 48, 'fps': 25,
                                          'fmt': 'RGB', 'backend': 'obs', 'device': 'OBS Virtual Camera'})
        np.testing.assert_array_equal(camera.frames[0], frame)
        np.testing.assert_array_equal(camera.frames[-1], status_frame(64, 48, 'MAT KET NOI'))
        self.assertEqual(len(camera.frames), 128)
        self.assertTrue(all(0 <= delay <= 0.040001 for delay in event.waits))
        self.assertEqual([statuses.get_nowait()[0] for _ in range(statuses.qsize())], ['starting', 'running', 'stopped'])

    def test_missing_or_busy_backend_reports_safe_actionable_error(self):
        shared = types.SimpleNamespace(width=64, height=48)
        for detail, expected in [('OBS Virtual Camera device not found! Did you install OBS?', 'OBS Studio'),
                                 ('virtual camera output could not be started rtsp://alice:secret@camera', 'Stop Virtual Camera')]:
            with self.subTest(detail=detail):
                def fail(**kwargs):
                    raise RuntimeError(detail)
                module = types.SimpleNamespace(Camera=fail, PixelFormat=types.SimpleNamespace(RGB='RGB'))
                statuses = queue.Queue()
                with patch.dict(sys.modules, {'pyvirtualcam': module}), \
                     patch.object(output, '_publisher_mutex', return_value=contextlib.nullcontext()):
                    output.run_output(shared, 25, statuses, ClockEvent(1))
                events = list(statuses.queue)
                self.assertEqual(events[-1][0], 'error')
                self.assertIn(expected, events[-1][1])
                self.assertNotIn('alice', str(events))
                self.assertNotIn('secret', str(events))

    def test_missing_python_backend_does_not_crash_worker(self):
        statuses = queue.Queue()
        with patch.dict(sys.modules, {'pyvirtualcam': None}), \
             patch.object(output, '_publisher_mutex', return_value=contextlib.nullcontext()):
            output.run_output(types.SimpleNamespace(width=64, height=48), 25, statuses, ClockEvent(1))
        self.assertEqual(list(statuses.queue)[-1][0], 'error')
        self.assertIn('pyvirtualcam', list(statuses.queue)[-1][1])

    def test_non_windows_has_friendly_error(self):
        statuses = queue.Queue()
        with patch.object(output.os, 'name', 'posix'):
            output.run_output(types.SimpleNamespace(width=64, height=48), 25, statuses, ClockEvent(1))
        self.assertEqual(list(statuses.queue)[-1][0], 'error')
        self.assertIn('Windows', list(statuses.queue)[-1][1])

    @unittest.skipUnless(os.name == 'nt', 'Windows named mutex')
    def test_second_publisher_is_rejected_and_lock_releases(self):
        with output._publisher_mutex():
            with self.assertRaises(output._OutputError):
                with output._publisher_mutex():
                    self.fail('Second publisher acquired the mutex')
        with output._publisher_mutex():
            pass


if __name__ == '__main__':
    unittest.main()


