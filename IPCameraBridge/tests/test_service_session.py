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
from PySide6.QtWidgets import QApplication, QLineEdit, QDialogButtonBox
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
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

    def test_checking_another_camera_switches_output_without_multiple_checks(self):
        session = ServiceSession('test-owner', start_worker=False)
        ids = [str(uuid4()), str(uuid4())]
        response = {'ok': True, '_command': 'status', 'revision': 1, 'generation': 0, 'data': {
            'pid': os.getpid(), 'config': {'revision': 1, 'resolution': '720p', 'auto_connect': False,
                'selected_id': ids[0], 'cameras': [dict(id=id, name=f'Camera {i}', kind='test', address='',
                    username='', has_password=False) for i, id in enumerate(ids)]},
            'cameras': [dict(id=id, state='connected', message='Đã kết nối', fps=15, wanted=True) for id in ids]}}
        try:
            session.status = response
            session.want_output = session.output_running = True
            session.open_window()
            window = session.window
            window.refresh()
            window.cameras.setCurrentRow(1)
            self.assertTrue(session.commands.empty())
            self.assertEqual(window.cameras.item(0).checkState(), Qt.CheckState.Checked)
            QTest.keyClick(window.cameras, Qt.Key.Key_Space)
            command = session.commands.get_nowait()
            self.assertEqual(command['command'], 'configure')
            self.assertEqual(command['update']['selected_id'], ids[1])
            self.assertEqual([window.cameras.item(i).checkState() for i in range(2)],
                             [Qt.CheckState.Unchecked, Qt.CheckState.Checked])
            response['_command'] = 'configure'
            response['data']['config']['selected_id'] = ids[1]
            window.receive(response)
            self.assertEqual(session.commands.get_nowait()['camera_id'], ids[1])
            response['_command'] = 'connect'
            window.receive(response)
            window.refresh()
            self.assertIn('Đang phát', window.cameras.item(1).text())
            session.results.put({'ok': False, 'code': 'offline'})
            session.tick()
            self.assertIsNone(session.status)
            self.assertNotIn('Đang phát', window.cameras.item(1).text())
            self.assertIn('Services', window.problems.toPlainText())
        finally:
            session.close()

    def test_scan_dialog_requires_valid_bounds_before_starting_worker(self):
        session = ServiceSession('test-owner', start_worker=False)
        try:
            session.open_window()
            window = session.window
            window.config = {'ready': True}
            def invalid_bounds():
                dialog = self.app.activeModalWidget()
                fields = dialog.findChildren(QLineEdit)
                fields[0].setText('192.168.1.30')
                fields[1].setText('192.168.1.10')
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
                self.assertTrue(dialog.isVisible())
                dialog.reject()
            with patch('ip_camera_bridge.service_session.scan_rtsp') as scan:
                QTimer.singleShot(0, invalid_bounds)
                window.scan_lan()
                scan.assert_not_called()
            def valid_bounds():
                dialog = self.app.activeModalWidget()
                fields = dialog.findChildren(QLineEdit)
                fields[0].setText('192.168.1.10')
                fields[1].setText('192.168.1.30')
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
            with patch('ip_camera_bridge.service_session.scan_rtsp', return_value=[]) as scan:
                QTimer.singleShot(0, valid_bounds)
                window.scan_lan()
                deadline = time.monotonic() + 2
                while window.discovery_results.empty() and time.monotonic() < deadline:
                    QTest.qWait(10)
                window.finish_scan()
                self.assertEqual(scan.call_args.args[0], '192.168.1.10')
                self.assertEqual(scan.call_args.kwargs['end'], '192.168.1.30')
                self.assertIn('dải đã nhập', window.notice.text())
            window.close()
            self.assertTrue(window.discovery_cancel.is_set())
            window.discovery_results.put([object()])
            window.finish_scan()  # Closing during a scan cannot show results with a cleared config.
        finally:
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
            window = session.window
            self.assertNotIn('Đang phát', window.cameras.item(0).text())
            self.assertTrue(window.password.isEnabled())
            self.assertFalse(window.preview.isVisible())
            self.assertTrue(window.remember.isChecked())
            window.auto.setChecked(True)
            window.remember.setChecked(False)
            self.assertFalse(window.auto.isChecked())
            window.auto.setChecked(True)
            self.assertTrue(window.remember.isChecked())
            window.auto.setChecked(False)
            window.address.setText('192.168.100.77')
            window.expand_address()
            self.assertEqual(window.address.text(), 'rtsp://192.168.100.77:554/Src/MediaInput/stream_1')
            window.address.clear()
            window.password.setText('sample@password')
            window.capture()
            self.assertEqual(window.config['cameras'][0]['password'], 'sample@password')
            window.password.clear()
            window.capture()
            self.assertEqual(window.config['cameras'][0]['password'], 'sample@password')
            window.use_camera()
            command = session.commands.get_nowait()
            self.assertEqual(command['command'], 'disconnect')
            response['_command'] = 'disconnect'
            response['data']['cameras'][0]['state'] = 'stopped'
            window.receive(response)
            command = session.commands.get_nowait()
            self.assertEqual(command['command'], 'configure')
            self.assertTrue(command['persist'])
            self.assertFalse(session.want_output)
            response['_command'] = 'configure'
            window.receive(response)
            self.assertEqual(session.commands.get_nowait(), {'command': 'connect', 'camera_id': camera_id})
            self.assertFalse(session.want_output)
            response['_command'] = 'connect'
            response['data']['cameras'][0]['state'] = 'connecting'
            window.receive(response)
            self.assertFalse(session.want_output)
            response['_command'] = 'status'
            response['data']['cameras'][0]['state'] = 'connected'
            window.receive(response)
            self.assertTrue(session.want_output)
            self.assertFalse(window.edit_button.isChecked())
            session.output_running = True
            window.refresh()
            self.assertIn('Đang phát', window.cameras.item(0).text())
            self.assertEqual(window.cameras.item(0).checkState(), Qt.CheckState.Checked)
            window.cameras.item(0).setCheckState(Qt.CheckState.Unchecked)
            self.assertFalse(session.want_output)
            window.cameras.item(0).setCheckState(Qt.CheckState.Checked)
            self.assertEqual(session.commands.get_nowait()['command'], 'configure')
            window.busy = False
            window.use_stage = None
            response['data']['cameras'][0].update(state='retrying', message='Camera từ chối xác thực (401) [code=825242872]')
            window.refresh()
            self.assertNotIn('code=', window.source_status.text())
            self.assertIn('mật khẩu', window.problems.toPlainText())
            history = window.problems.toPlainText()
            window.refresh()
            self.assertEqual(history, window.problems.toPlainText())
            self.assertNotIn('Đang phát', window.cameras.item(0).text())
            window.add_camera()
            self.assertEqual(window.config['cameras'][-1]['kind'], 'rtsp')
            window.clear_password()
            window.capture()
            self.assertEqual(window.config['cameras'][-1]['password'], '')
            session.stop_output()
            window.use_stage = 'waiting'
            window.toggle_output()
            self.assertIsNone(window.use_stage)
            self.assertFalse(session.want_output)
            window.use_stage = 'waiting'
            response['data']['cameras'][0]['state'] = 'error'
            window.receive(response)
            self.assertIsNone(window.use_stage)
            self.assertFalse(session.want_output)
            window.reload()
            window.refresh()  # Reload may temporarily have no form config.
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
