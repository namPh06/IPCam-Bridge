import multiprocessing as mp
import os
import time
import unittest
import tempfile
import copy
import win32api
import win32security
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from ip_camera_bridge.frames import SharedFrame, status_frame
from ip_camera_bridge.service_session import ServiceSession
from ip_camera_bridge import service_ipc as ipc
from ip_camera_bridge.service_runtime import CaptureRuntime
from ip_camera_bridge.windows_service import ControlQueue


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_frame_keeps_capture_time_and_rejects_old_generation(self):
        session = ServiceSession('test-owner', start_worker=False)
        try:
            rgb = status_frame(1280, 720, 'TEST')
            stamp = time.monotonic() - 10
            session.generation = 4
            session.accept_frame((3, 1, stamp, rgb))
            self.assertIsNone(session.shared.read())
            session.accept_frame((4, 2, stamp, rgb))
            self.assertEqual(session.shared.read()[1], stamp)
            session.stop_output()
            self.assertFalse(session.want_output)
        finally:
            session.close()

    def test_publisher_restart_replaces_abandoned_frame_lock(self):
        session = ServiceSession('test-owner', start_worker=False)
        abandoned = session.shared
        abandoned._lock.acquire()
        try:
            session.status = {'ok': True}
            session.start_output()
            with patch.object(session.output, 'start') as start:
                session.tick()
            self.assertIsNot(session.shared, abandoned)
            self.assertIsNotNone(session.shared.read())
            start.assert_called_once()
        finally:
            abandoned._lock.release()
            session.close()

    def test_window_close_keeps_session_and_has_no_local_decoders(self):
        session = ServiceSession('test-owner', start_worker=False)
        try:
            session.open_window()
            self.assertFalse(hasattr(session.window, 'group'))
            session.window.close()
            self.assertFalse(session.window.isVisible())
            self.assertFalse(session.stop.is_set())
        finally:
            session.close()

    def test_service_window_marks_output_and_uses_stateful_action_buttons(self):
        session = ServiceSession('test-owner', start_worker=False)
        camera_id = str(uuid4())
        response = {'ok': True, '_command': 'status', 'revision': 0, 'generation': 0, 'data': {
            'pid': os.getpid(), 'sequence': 0,
            'config': {'revision': 0, 'resolution': '720p', 'auto_connect': False,
                       'selected_id': camera_id, 'cameras': [{
                           'id': camera_id, 'name': 'Sảnh chính', 'kind': 'test', 'address': '',
                           'username': '', 'has_password': False}]},
            'cameras': [{'id': camera_id, 'state': 'connected', 'message': 'Đã kết nối',
                         'fps': 15.0, 'wanted': True}]}}
        try:
            session.want_output = False
            session.status = response
            session.open_window()
            session.window.receive(response)
            session.window.refresh()
            self.assertIn('ĐANG XUẤT', session.window.cameras.itemText(0))
            self.assertEqual(session.window.output_select_button.text(), '✓ Camera đang xuất')
            self.assertEqual(session.window.connect_button.text(), 'Ngắt camera đang chọn')
            self.assertEqual(session.window.output_button.text(), 'Bật webcam ảo')
            session.window.toggle_selected()
            self.assertEqual(session.commands.get_nowait()['command'], 'disconnect')
        finally:
            session.close()

    def test_real_pipe_session_receives_capture_and_manual_output_stop_survives_status(self):
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        owner = win32security.ConvertSidToStringSid(sid)
        token.Close()
        stop = __import__('threading').Event()
        names = tuple(r'\\.\pipe\ipcb-session-test-' + str(uuid4()) for _ in range(2))
        with tempfile.TemporaryDirectory() as directory, patch.object(ipc, 'PIPE_NAMES', names), \
                patch.object(ipc, '_service_sid', return_value=sid), patch.object(ipc, '_verify_server'):
            runtime = CaptureRuntime(Path(directory) / 'profiles.dat')
            runtime.start()
            config = copy.deepcopy(runtime.config)
            config['auto_connect'] = True
            self.assertTrue(runtime.handle({'command': 'configure', 'update': config})['ok'])
            commands = ControlQueue(stop)
            server = ipc.PipeServer(owner, stop)
            server.start(commands.request, runtime.frame)
            session = ServiceSession(owner)
            session.stop_output()
            try:
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline:
                    commands.drain(runtime)
                    runtime.tick()
                    self.app.processEvents()
                    if runtime.group.current.source_state == 'connected' and session.shared.read() is not None:
                        break
                    time.sleep(.02)
                self.assertEqual(runtime.group.current.source_state, 'connected')
                self.assertIsNotNone(session.shared.read())
                self.assertFalse(session.want_output)
                self.assertFalse(session.output.active)
            finally:
                session.close()
                server.close()
                runtime.close()
                deadline = time.monotonic() + 3
                while (not runtime.closed or session.worker.is_alive()) and time.monotonic() < deadline:
                    runtime.tick()
                    self.app.processEvents()
                    time.sleep(.02)
                self.assertFalse(session.worker.is_alive())
                self.assertTrue(runtime.closed)
