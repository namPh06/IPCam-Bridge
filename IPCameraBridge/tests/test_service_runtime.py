import contextlib
import copy
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import av
import numpy as np

from ip_camera_bridge.service_runtime import CaptureRuntime
from ip_camera_bridge.sources import SourceSpec, run_source


class RuntimeTests(unittest.TestCase):
    def test_temporary_configuration_never_overwrites_saved_cameras(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profiles.dat'
            runtime = CaptureRuntime(path)
            runtime.start()
            try:
                config = copy.deepcopy(runtime.config)
                self.assertTrue(runtime.handle({'command': 'configure', 'update': config})['ok'])
                saved = path.read_bytes()
                config = copy.deepcopy(runtime.config)
                config['cameras'][0]['password'] = 'temporary@secret'
                self.assertTrue(runtime.handle({'command': 'configure', 'update': config, 'persist': False})['ok'])
                self.assertEqual(path.read_bytes(), saved)
                self.assertTrue(runtime.handle({'command': 'select', 'camera_id': config['selected_id'],
                                                'revision': runtime.config['revision']})['ok'])
                self.assertEqual(path.read_bytes(), saved)
                config = copy.deepcopy(runtime.config)
                config['auto_connect'] = True
                self.assertFalse(runtime.handle({'command': 'configure', 'update': config, 'persist': False})['ok'])
                config['auto_connect'] = False
                self.assertTrue(runtime.handle({'command': 'configure', 'update': config})['ok'])
                self.assertNotEqual(path.read_bytes(), saved)
                self.assertNotIn(b'temporary@secret', path.read_bytes())
            finally:
                runtime.close()

    def test_empty_camera_is_saved_but_skipped_for_auto_and_manual_connect_all(self):
        with tempfile.TemporaryDirectory() as directory, patch('ip_camera_bridge.controller.BridgeController.connect') as connect:
            path = Path(directory) / 'profiles.dat'
            runtime = CaptureRuntime(path)
            runtime.start()
            restored = None
            try:
                config = copy.deepcopy(runtime.config)
                config['cameras'][0].update(kind='rtsp', address='rtsp://camera.example/live')
                draft = dict(config['cameras'][0], id=str(uuid4()), name='Draft', address='')
                config['cameras'].append(draft)
                config['auto_connect'] = True
                result = runtime.handle({'command': 'configure', 'update': config})
                self.assertTrue(result['ok'])
                self.assertEqual(connect.call_count, 1)
                self.assertNotIn(draft['id'], runtime.wanted)
                self.assertFalse(result['data']['cameras'][1]['wanted'])
                self.assertIn('Chưa nhập', result['data']['cameras'][1]['message'])
                runtime.handle({'command': 'connect', 'camera_id': None})
                self.assertNotIn(draft['id'], runtime.wanted)
                restored = CaptureRuntime(path)
                restored.start()
                self.assertEqual(restored.config['cameras'][1]['address'], '')
                self.assertNotIn(draft['id'], restored.wanted)
                self.assertIn(config['selected_id'], restored.wanted)
                invalid = copy.deepcopy(restored.config)
                invalid['cameras'][1]['address'] = 'not-a-url'
                self.assertFalse(restored.handle({'command': 'configure', 'update': invalid})['ok'])
            finally:
                runtime.close()
                if restored is not None:
                    restored.close()

    def test_service_rtsp_recovers_after_more_than_eight_failures(self):
        stop, statuses, delays = threading.Event(), queue.Queue(100), []
        image = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), np.uint8), format='rgb24')
        def decode(_):
            yield image
            stop.set()
        container = SimpleNamespace(streams=SimpleNamespace(video=[object()]), decode=decode)
        shared = SimpleNamespace(width=64, height=48, publish=lambda _: True)
        spec = SourceSpec(kind='rtsp', address='rtsp://localhost/live', max_retries=None, retry_cap=30)
        with patch('ip_camera_bridge.sources.av.open', side_effect=[OSError()] * 10 + [contextlib.nullcontext(container)]), \
                patch.object(stop, 'wait', side_effect=lambda delay: delays.append(delay) or stop.is_set()):
            run_source(spec, shared, 25, statuses, stop)
        self.assertIn('connected', [state for state, _ in statuses.queue])
        self.assertEqual(max(delays), 30)
        self.assertEqual(len(delays), 10)

    def test_durable_config_selection_and_failed_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profiles.dat'
            runtime = CaptureRuntime(path)
            runtime.start()
            try:
                config = copy.deepcopy(runtime.config)
                second = dict(config['cameras'][0], id=str(uuid4()), name='Second')
                config['cameras'].append(second)
                self.assertTrue(runtime.handle({'command': 'configure', 'update': config})['ok'])
                malicious = {'revision': 1, 'cameras': []}
                value = []
                for _ in range(600):
                    value = [value]
                malicious['extra'] = value
                self.assertFalse(runtime.handle({'command': 'configure', 'update': malicious})['ok'])
                first_controller = runtime.group.cameras[0]
                first_controller._image[:] = 255
                old_generation = runtime.frame()[0]
                result = runtime.handle({'command': 'select', 'camera_id': second['id'], 'revision': 1})
                self.assertTrue(result['ok'])
                self.assertGreater(runtime.frame()[0], old_generation)
                self.assertFalse(np.all(runtime.frame()[3] == 255))
                self.assertIs(runtime.group.cameras[0], first_controller)
                with patch('ip_camera_bridge.service_runtime.save_service_config', side_effect=ValueError):
                    result = runtime.handle({'command': 'select', 'camera_id': config['selected_id'], 'revision': 2})
                    self.assertEqual(result['code'], 'save_failed')
                    self.assertEqual(runtime.config['revision'], 2)
                    self.assertEqual(runtime.config['selected_id'], second['id'])
                restored = CaptureRuntime(path)
                restored.start()
                self.assertEqual(restored.config['selected_id'], second['id'])
                restored.close()
            finally:
                runtime.close()

    def test_two_decoders_and_missing_file_manual_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = CaptureRuntime(Path(directory) / 'profiles.dat')
            runtime.start()
            try:
                config = copy.deepcopy(runtime.config)
                config['cameras'].append(dict(config['cameras'][0], id=str(uuid4()), name='Missing',
                                              kind='file', address=str(Path(directory) / 'missing.avi')))
                config['auto_connect'] = True
                self.assertTrue(runtime.handle({'command': 'configure', 'update': config})['ok'])
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and runtime.group.cameras[0].source_state != 'connected':
                    runtime.tick()
                    time.sleep(.04)
                self.assertEqual(runtime.group.cameras[0].source_state, 'connected')
                update = copy.deepcopy(runtime.config)
                update['cameras'][0]['password'] = 'changed'
                self.assertEqual(runtime.handle({'command': 'configure', 'update': update})['code'], 'source_busy')
                runtime.handle({'command': 'disconnect', 'camera_id': None})
                for _ in range(60):
                    runtime.tick()
                    time.sleep(.02)
                self.assertFalse(runtime.group.cameras[0].source.active)
                self.assertFalse(runtime.wanted)
            finally:
                runtime.close()
                deadline = time.monotonic() + 3
                while not runtime.closed and time.monotonic() < deadline:
                    runtime.tick()
                    time.sleep(.02)
                self.assertTrue(runtime.closed)
